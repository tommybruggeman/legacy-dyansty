from __future__ import annotations

from collections import Counter,defaultdict
from dataclasses import asdict
from decimal import Decimal
from datetime import datetime,timezone
import json,logging,os

from .contract_read_models import ContractReadRecord,HistoricalContractSeasonRead
from .contract_read_repository import NormalizedContractReadRepository
from .legacy_contract_adapter import project_legacy_contract_shape
from .operational_season import resolve_contract_operational_season


class ContractReadValidationError(RuntimeError):pass


class ContractReadService:
    def __init__(self,client,mode=None,diagnostic_sink=None):
        self.client=client;self.mode=(mode or os.getenv("CONTRACT_READ_MODE","normalized")).lower();self.diagnostic_sink=diagnostic_sink
        if self.mode not in {"legacy","compare","normalized"}:raise ValueError("CONTRACT_READ_MODE must be legacy, compare, or normalized")
    def get_contract_operational_season(self,league_id):return resolve_contract_operational_season(self.client,league_id)
    def get_contracts(self,league_id):
        operational=self.get_contract_operational_season(league_id); data=NormalizedContractReadRepository(self.client).load(league_id)
        return self._build(league_id,operational,data),data
    def get_active_contracts(self,league_id):return [x for x in self.get_contracts(league_id)[0] if x.agreement_status=="active"]
    def get_expired_contracts(self,league_id):return [x for x in self.get_contracts(league_id)[0] if x.agreement_status=="expired"]
    def get_team_active_contracts(self,league_id,team_id):return [x for x in self.get_active_contracts(league_id) if x.canonical_team_id==team_id]
    def get_player_active_contract(self,league_id,player_id):
        rows=[x for x in self.get_active_contracts(league_id) if x.player_id==str(player_id)]
        if len(rows)>1:raise ContractReadValidationError("Multiple active agreements exist for player.")
        return rows[0] if rows else None
    def get_contract_by_agreement_id(self,league_id,agreement_id):
        rows=[x for x in self.get_contracts(league_id)[0] if x.agreement_id==str(agreement_id)];return rows[0] if len(rows)==1 else None
    def get_contract_history(self,league_id,agreement_id):
        record=self.get_contract_by_agreement_id(league_id,agreement_id);return tuple(record.provenance["season_reads"]) if record else ()
    def get_contract_future_schedule(self,league_id,agreement_id):
        record=self.get_contract_by_agreement_id(league_id,agreement_id);return record.future_contract_seasons if record else ()
    def get_team_summary(self,league_id):
        records,_=self.get_contracts(league_id); out=[]
        for team_id in sorted({x.canonical_team_id for x in records}):
            own=[x for x in records if x.canonical_team_id==team_id];active=[x for x in own if x.agreement_status=="active"];expired=[x for x in own if x.agreement_status=="expired"]
            history=[s for x in expired for s in x.provenance["season_reads"] if s.season==2025]
            out.append({"league_team_id":team_id,"team_name":own[0].canonical_team_name,"active_contract_count":len(active),
                "active_operational_salary":sum((x.operational_salary or Decimal("0")) for x in active),"expired_contract_count":len(expired),
                "expired_prior_season_salary":sum(s.salary for s in history),"future_2027_salary":sum(s.salary for x in active for s in x.future_contract_seasons if s.season==2027),
                "cap_adjustments_included":False})
        return out
    def project_legacy_contract_shape(self,league_id,include_expired=True):
        records,data=self.get_contracts(league_id);legacy={str(x["id"]):x for x in data["legacy"]}
        return [project_legacy_contract_shape(x,legacy.get(str(x.source_legacy_contract_id))).values for x in records if include_expired or x.agreement_status=="active"]
    def read_compatible(self,league_id,caller="unknown"):
        normalized=self.project_legacy_contract_shape(league_id);legacy=self.client.table("contracts").select("*").eq("league_id",league_id).execute().data or []
        if self.mode=="legacy":return legacy
        diagnostics=compare_normalized_and_legacy_reads(normalized,legacy,self.get_contract_operational_season(league_id),league_id,caller,self.mode)
        if self.diagnostic_sink:self.diagnostic_sink(diagnostics)
        else:logging.getLogger(__name__).info("contract_read_comparison %s",json.dumps(diagnostics,default=str,sort_keys=True))
        return legacy if self.mode=="compare" else normalized
    def _certified_source_reconciliation(self,league_id,operational,data):
        reconciliations = data.get("reconciliations") or []
        matches = [
            row for row in reconciliations
            if str(row.get("league_id")) == str(league_id)
            and str(row.get("reconciliation_status")) == "certified"
            and int(row.get("source_season") or 0) == int(operational)
        ]
        if len(matches) > 1:
            raise ContractReadValidationError(
                "Multiple certified reconciliations exist for operational source season."
            )
        return matches[0] if matches else None

    def _build(self,league_id,operational,data):
        agreements=data["agreements"]; schedules=defaultdict(list)
        for row in data["seasons"]:schedules[str(row["contract_id"])].append(row)
        teams={str(x["id"]):x for x in data["teams"]};players={str(x["sleeper_id"]):x for x in data["players"]}; records=[];seen_players=set()
        reconciliation=self._certified_source_reconciliation(league_id,operational,data)
        for a in sorted(agreements,key=lambda x:str(x["id"])):
            aid=str(a["id"]);pid=str(a["player_id"]);tid=str(a["league_team_id"]);rows=sorted(schedules[aid],key=lambda x:int(x["season"]));by_year=defaultdict(list)
            for row in rows:by_year[int(row["season"])].append(row)
            if any(len(x)!=1 for x in by_year.values()):raise ContractReadValidationError(f"Duplicate obligation for {aid}.")
            years=sorted(by_year); expected=list(range(years[0],years[-1]+1)) if years else []
            if years!=expected:raise ContractReadValidationError(f"Obligation gap for {aid}.")
            if tid not in teams or pid not in players:raise ContractReadValidationError(f"Missing team/player identity for {aid}.")
            op=(by_year.get(operational) or [None])[0];status=str(a["status"])
            if status=="active" and (not op or op.get("obligation_status")!="active"):raise ContractReadValidationError(f"Active agreement {aid} lacks active operational obligation.")
            if status=="expired" and op and op.get("obligation_status")=="active" and reconciliation is None:
                raise ContractReadValidationError(f"Expired agreement {aid} has active operational obligation.")
            if status=="active" and pid in seen_players:raise ContractReadValidationError(f"Duplicate active player {pid}.")
            if status=="active":seen_players.add(pid)
            season_reads=tuple(HistoricalContractSeasonRead(str(x["id"]),aid,int(x["season"]),Decimal(str(x["salary"])),str(x["obligation_status"]),tid,pid,
                "historical" if int(x["season"])<operational else "current" if int(x["season"])==operational else "future") for x in rows)
            remaining=sum(x.season>=operational and x.obligation_status in {"active","scheduled"} for x in season_reads) if status=="active" else 0
            salary=Decimal(str(op["salary"])) if status=="active" and op else None
            if salary is not None and salary<0:raise ContractReadValidationError(f"Negative operational salary for {aid}.")
            player=players[pid];team=teams[tid]
            records.append(ContractReadRecord(aid,league_id,tid,team.get("team_name") or team.get("owner_name"),pid,str(a.get("sleeper_player_id")),
                player.get("player_name"),player.get("pos"),status,operational,str(op["id"]) if op else None,op.get("obligation_status") if op else None,salary,
                int(a["end_season"]),remaining,tuple(x for x in season_reads if x.season>operational),a.get("contract_type"),a.get("source_legacy_contract_id"),
                {"authority":"normalized","operational_season":operational,"season_reads":season_reads,"schedule_count":len(rows)},()))
        execution=next((x for x in data["executions"] if x.get("status")=="validated" and int(x.get("target_season") or 0)==operational),None)
        expected=((execution or {}).get("result") or {}).get("persisted") or {}
        if expected and (len([x for x in records if x.agreement_status=="active"])!=int(expected["active_agreements"])
            or len([x for x in records if x.agreement_status=="expired"])!=int(expected["expired_agreements"])):
            raise ContractReadValidationError("Normalized read counts differ from validated execution.")
        return records


def compare_normalized_and_legacy_reads(normalized,legacy,operational_season,league_id,caller,mode):
    n={str(x.get("sleeper_player_id")):x for x in normalized};l={str(x.get("sleeper_player_id")):x for x in legacy}
    shared=sorted(set(n)&set(l))
    return {"comparison_timestamp":datetime.now(timezone.utc).isoformat(),"league_id":league_id,"caller":caller,"read_mode":mode,"operational_season":operational_season,"legacy_row_count":len(legacy),
        "normalized_row_count":len(normalized),"player_differences":sorted(set(n)^set(l)),
        "team_differences":[pid for pid in shared if n[pid].get("owner_name")!=l[pid].get("owner_name")],
        "salary_differences":[pid for pid in shared if n[pid].get("salary")!=l[pid].get("salary")],
        "years_remaining_differences":[pid for pid in shared if n[pid].get("contract_years_left")!=l[pid].get("contract_years_left")],
        "lifecycle_status_differences":[pid for pid in shared if n[pid].get("status")!="active"],
        "fallback_fields":sorted({k for x in normalized for k,v in (x.get("provenance") or {}).items() if "fallback" in str(v)})}
