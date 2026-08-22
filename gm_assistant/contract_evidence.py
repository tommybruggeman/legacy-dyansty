from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any,Mapping

from services.trade_contract_evidence import TradeContractEvidenceService


@dataclass(frozen=True)
class GMContractEvidence:
    agreement_id:str;league_id:str;player_id:str;sleeper_player_id:str;player_name:str
    canonical_team_id:str;team_name:str;agreement_status:str;contract_operational_season:int
    operational_salary:Decimal|None;years_remaining:int;expiration_season:int
    historical_obligations:tuple[dict[str,Any],...];future_obligations:tuple[dict[str,Any],...]
    salary_2027:Decimal;roster_status:str;roster_team_id:str|None;free_agent_publication_status:str
    trade_eligibility_status:str;trade_legality_status:str;lifecycle_classification:str
    source_legacy_contract_id:str|None;provenance:Mapping[str,object];warnings:tuple[str,...];uncertainty:tuple[str,...]

    def to_row(self)->dict[str,Any]:
        row=dict(self.__dict__);row["provenance"]=dict(self.provenance);row["historical_obligations"]=[dict(x) for x in self.historical_obligations];row["future_obligations"]=[dict(x) for x in self.future_obligations]
        row.update({"league_team_id":self.canonical_team_id,"team_id":self.canonical_team_id,"owner_name":self.team_name,
            "salary":float(self.operational_salary) if self.operational_salary is not None else None,
            "contract_years_left":self.years_remaining,"season":self.contract_operational_season,
            "contract_status":self.agreement_status,"status":self.agreement_status,"sleeper_id":self.sleeper_player_id,
            "future_2027_salary":float(self.salary_2027),"data_authority":"normalized_contract_model"})
        return row


class GMContractEvidenceService:
    def __init__(self,sb,*,mode="normalized",diagnostic_sink=None):self.sb=sb;self.mode=mode;self.diagnostic_sink=diagnostic_sink

    def load(self,league_id:str)->tuple[GMContractEvidence,...]:
        roster,league_season=self._roster(league_id);published=self._published_free_agents(league_id)
        trade=TradeContractEvidenceService(self.sb,mode=self.mode,diagnostic_sink=self.diagnostic_sink)
        trade_rows=trade.load_trade_contract_evidence(league_id,roster)
        contract_season=trade_rows[0].contract_operational_season if trade_rows else league_season
        mixed_season=league_season!=contract_season
        out=[]
        for item in trade_rows:
            history=tuple({"season":x.season,"salary":float(x.salary),"obligation_status":x.obligation_status,"temporal_role":x.temporal_role} for x in item.provenance.get("season_reads",()) if x.temporal_role=="historical")
            future=tuple({"season":x.season,"salary":float(x.salary),"obligation_status":x.obligation_status} for x in item.future_contract_schedule)
            published_status="unknown" if published is None else "published" if item.player_id in published or item.sleeper_player_id in published else "not_published"
            lifecycle={"rostered_active_contract":"ACTIVE_ROSTERED_CONTRACT","active_off_roster_liability":"ACTIVE_OFF_ROSTER_LIABILITY","rostered_contract_expired":"ROSTERED_CONTRACT_EXPIRED","unrostered_contract_unbound":"EXPIRED_UNROSTERED"}.get(item.roster_classification,"HISTORICAL_ONLY")
            uncertainty=[]
            if published_status=="unknown":uncertainty.append("free_agent_publication_source_unavailable")
            if lifecycle=="ROSTERED_CONTRACT_EXPIRED":uncertainty.append("broader_roster_and_free_agent_workflow_pending")
            eligibility="roster_authority_required" if not item.roster_team_id else "roster_owned_not_full_legality"
            legality="mixed_season_legality_deferred" if mixed_season else "verified_contract_value_only"
            out.append(GMContractEvidence(item.agreement_id,item.league_id,item.player_id,item.sleeper_player_id,item.player_name,item.canonical_team_id,item.team_name,item.agreement_status,item.contract_operational_season,item.operational_salary,item.years_remaining,item.expiration_season,history,future,item.future_2027_salary,"rostered" if item.roster_team_id else "unrostered",item.roster_team_id,published_status,eligibility,legality,lifecycle,item.source_legacy_contract_id,item.provenance,item.warnings,tuple(uncertainty)))
        return tuple(out)

    def classify_missing_player(self,*,canonical_identity_resolved:bool)->str:
        return "NO_NORMALIZED_AGREEMENT" if canonical_identity_resolved else "IDENTITY_OR_DATA_FAILURE"

    def _roster(self,league_id):
        seasons=self.sb.table("league_seasons").select("*").eq("league_id",league_id).execute().data or [];active=[x for x in seasons if x.get("is_active")]
        if len(active)!=1:raise RuntimeError("GM contract evidence requires exactly one active league season.")
        rows=self.sb.table("season_roster_assignments").select("*").eq("league_season_id",str(active[0]["id"])).execute().data or []
        return {str(x.get("canonical_player_id") or x.get("sleeper_player_id")):str(x["league_team_id"]) for x in rows},int(active[0]["season"])
    def _published_free_agents(self,league_id):
        try:rows=self.sb.table("free_agents").select("*").eq("league_id",league_id).execute().data or []
        except Exception:return None
        return {str(x.get("canonical_player_id") or x.get("sleeper_player_id") or x.get("player_id")) for x in rows}
