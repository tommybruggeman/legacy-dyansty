"""Phase 3B.4G production analysis. Performs SELECTs only."""
from __future__ import annotations
from dataclasses import asdict
import hashlib,json

from auth import service_client
from season_engine.commissioner_policy_draft import CommissionerPolicyDraftService,RELEASE_TO_HOLD,SEVEN_DAY_NOTICE_RULE
from season_engine.rollover_service import RolloverAuthorityService

TABLES=("league_seasons","league_rules","league_rollover_policies","free_agent_publications","dead_cap_obligations","dead_cap_season_authorities","cap_season_authorities","contracts","contract_agreements","contract_seasons","contract_events","contract_transition_executions","cap_adjustments","dead_cap_ledger","draft_picks","transaction_ledger","gm_user_memory")
def read(client,table,league):
    try:return client.table(table).select("*").eq("league_id",league).execute().data or []
    except Exception:return None
def fp(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()

def main():
    client=service_client();seasons=client.table("league_seasons").select("*").eq("is_active",True).execute().data or []
    if len(seasons)!=1:raise RuntimeError("Exactly one active season required")
    league=str(seasons[0]["league_id"]);before={t:fp(x) if (x:=read(client,t,league)) is not None else "unavailable" for t in TABLES}
    report=RolloverAuthorityService(client).build_rollover_readiness_report(league);service=CommissionerPolicyDraftService();draft=service.prepare(league,deadline=SEVEN_DAY_NOTICE_RULE,failure_to_act_outcome=RELEASE_TO_HOLD)
    impact=service.reduce(draft,report.roster_exceptions);packet=service.approval_packet(draft,impact)
    universe=client.table("player_universe").select("*").in_("sleeper_id",list(impact["individual_review_players"])).execute().data or []
    universe_by={str(x.get("sleeper_id")):x for x in universe}
    candidates=[];off=[]
    for item in report.roster_exceptions:
        player=universe_by.get(item.player_id,{})
        evidence={"player_id":item.player_id,"player_name":item.player_name,"prior_team_id":item.team_id,"roster_state":item.roster_status,"contract_state":item.contract_status,
            "historical_obligation":"2025 satisfied","active_obligation":item.evidence.get("salary"),"rookie_draft_state":{"rookie_class_year":player.get("rookie_class_year"),"draft_year":player.get("draft_year"),"market_pool":player.get("market_pool")},
            "waiver_state":"unknown","publication_state":"not_initialized","proposed_disposition":"commissioner_hold_pending_rollover_resolution","acquisition_status":"ineligible",
            "blockers":["commissioner policy approval required","publication authority not initialized"],"warnings":[],"lineage":["contract_agreements","contract_seasons","season_roster_assignments","player_universe"]}
        if item.classification=="EXPIRED_UNROSTERED_PUBLICATION_PENDING":candidates.append(evidence)
        elif item.classification=="ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED":
            evidence.update({"agreement_id":item.agreement_id,"years_remaining":item.evidence.get("years_remaining"),"future_obligations":item.evidence.get("future_obligations"),"termination_evidence":"none","liability_remains":True,"publication_allowed":False,"acquisition_allowed":False,"second_agreement_blocked":True,"dead_cap":"only after future qualifying early termination","choices":["retain liability","approve explicit early-termination review","commissioner evidence review"]});off.append(evidence)
    after={t:fp(x) if (x:=read(client,t,league)) is not None else "unavailable" for t in TABLES}
    out={"draft":asdict(draft),"readiness":service.readiness(draft),"scenarios":[asdict(x) for x in service.scenarios()],"decision_impact":impact,"approval_packet":asdict(packet),"publication_candidates":candidates,"active_off_roster":off,
        "deadline_evidence":"commissioner selected recurring seven-calendar-day rule","failure_to_act_evidence":"commissioner selected release at rollover to commissioner hold","taxi_evidence":"rookie eligibility and in-season lock documented; rollover disposition not encoded","ir_evidence":"IR does not change contract lifecycle; rollover disposition not encoded",
        "authority_rows":{"policy":len(read(client,"league_rollover_policies",league) or []),"publication":len(read(client,"free_agent_publications",league) or []),"dead_cap_obligations":len(read(client,"dead_cap_obligations",league) or []),"dead_cap_authorities":len(read(client,"dead_cap_season_authorities",league) or []),"cap_authorities":len(read(client,"cap_season_authorities",league) or [])},
        "before":before,"after":after,"changed":[t for t in TABLES if before[t]!=after[t]],"writes_performed":0}
    print(json.dumps(out,indent=2,sort_keys=True,default=str))
if __name__=="__main__":main()
