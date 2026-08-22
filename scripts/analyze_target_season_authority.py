"""Phase 3B.4E read-only production analysis. SELECTs only."""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
import hashlib,json

from auth import service_client
from season_engine.rollover_service import RolloverAuthorityService
from season_engine.target_authority import CommissionerPolicyService,TargetAuthorityRepository

TABLES=("league_seasons","league_rules","league_settings","contracts","contract_agreements","contract_seasons","contract_events","contract_transition_executions","cap_adjustments","dead_cap_ledger","free_agent_publications","dead_cap_obligations","dead_cap_season_authorities","cap_season_authorities","league_rollover_policies","draft_picks","transaction_ledger","gm_user_memory")

def read(client,table,league):
    try:return client.table(table).select("*").eq("league_id",league).execute().data or [],None
    except Exception as exc:return [],type(exc).__name__
def fingerprint(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()

def main():
    client=service_client();active=client.table("league_seasons").select("*").eq("is_active",True).execute().data or []
    if len(active)!=1:raise RuntimeError("Exactly one active development league season required")
    league=str(active[0]["league_id"]);before={};errors={}
    for table in TABLES:
        rows,error=read(client,table,league);before[table]=fingerprint(rows) if not error else "unavailable:"+error;errors[table]=error
    report=RolloverAuthorityService(client).build_rollover_readiness_report(league)
    repository=TargetAuthorityRepository(client)
    policy_state=repository.policy_state(league,2026)
    publication_state=repository.publication_state(league,2026)
    dead_cap_state=repository.dead_cap_state(league,2026)
    cap_state=repository.cap_state(league,2026)
    after={}
    for table in TABLES:
        rows,error=read(client,table,league);after[table]=fingerprint(rows) if not error else "unavailable:"+error
    rule_rows,_=read(client,"league_rules",league);rules=rule_rows[0] if rule_rows else {}
    options=[asdict(x) for x in CommissionerPolicyService().options(108)]
    cap=[]
    for item in report.projected_caps:
        zero_total=item.active_contract_salary+(item.cap_adjustments or Decimal("0"))
        cap.append({"team":item.team_name,"team_id":item.league_team_id,"contract_salary":str(item.active_contract_salary),"adjustment":str(item.cap_adjustments),"current_dead_cap":"unknown","provisional_if_initialized_zero_total":str(zero_total),"provisional_if_initialized_zero_available":str((item.salary_cap_limit-zero_total) if item.salary_cap_limit is not None else None),"authoritative":False})
    groups={}
    for item in report.roster_exceptions:groups.setdefault(item.classification,[]).append({"player":item.player_name,"player_id":item.player_id,"team_id":item.team_id,"salary":item.evidence.get("salary"),"years":item.evidence.get("years_remaining"),"future":item.evidence.get("future_obligations"),"action":item.proposed_action})
    schema_available=all(errors.get(x) is None for x in ("league_rollover_policies","free_agent_publications","dead_cap_obligations","dead_cap_season_authorities","cap_season_authorities"))
    integrated=RolloverAuthorityService(client).integrate_target_authorities(report,schema_available=schema_available,policy=None,publication_authority_status=publication_state["status"],dead_cap_authority_status=dead_cap_state["status"],cap_authority_status=cap_state["status"])
    out={"authority":{"league":2025,"cap":2025,"contracts":2026,"target":2026},"existing_policy_evidence":{"salary_cap":rules.get("salary_cap"),"default_dead_cap_pct":rules.get("default_dead_cap_pct"),"default_fa_years":rules.get("default_fa_years"),"default_waiver_salary":rules.get("default_waiver_salary"),"rollover_policy_encoded":False},"typed_empty_states":{"policy":policy_state,"publication":publication_state,"dead_cap":dead_cap_state,"cap":cap_state},"policy_options":options,"exception_counts":dict(Counter(x.classification for x in report.roster_exceptions)),"exceptions":groups,"publication_candidates":groups.get("EXPIRED_UNROSTERED_PUBLICATION_PENDING",[]),"new_authority_objects":{"league_rollover_policies":errors.get("league_rollover_policies") is None,"free_agent_publications":errors.get("free_agent_publications") is None,"dead_cap_obligations":errors.get("dead_cap_obligations") is None,"dead_cap_season_authorities":errors.get("dead_cap_season_authorities") is None,"cap_season_authorities":errors.get("cap_season_authorities") is None},"existing_2026_dead_cap_obligations":len(dead_cap_state["obligations"]),"authoritative_2026_zero_proven":dead_cap_state["authoritative_zero"],"cap_sign_convention":"positive consumes cap; negative creates cap credit","provisional_zero_dead_cap_projection":cap,"readiness":integrated,"blockers":{"policy_approval":["commissioner_choices_not_selected","deadline/taxi/ir/publication outcomes unresolved"],"authority_initialization":["policy_not_approved","publication authority not initialized","dead-cap authoritative zero not initialized","cap authority not validated"],"rollover_execution":[*report.blockers,"write-path cutovers not implemented"],"visible_cutover":["league/cap authority remains 2025","target authority not validated","rollover not executed"]},"before":before,"after":after,"changed":[x for x in before if before[x]!=after[x]],"writes_performed":0}
    print(json.dumps(out,indent=2,sort_keys=True,default=str))

if __name__=="__main__":main()
