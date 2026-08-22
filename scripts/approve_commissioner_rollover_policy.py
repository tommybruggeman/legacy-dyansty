"""Phase 3B.4H: approve exactly one policy row; no other writes."""
from __future__ import annotations
from dataclasses import asdict
import hashlib,json

from auth import service_client
from season_engine.commissioner_policy_approval import CommissionerPolicyApprovalService
from season_engine.commissioner_policy_draft import CommissionerPolicyDraftService,RELEASE_TO_HOLD,SEVEN_DAY_NOTICE_RULE
from season_engine.rollover_service import stable_fingerprint

LEAGUE="9838a0a1-97c6-4cab-bb88-af177317abfe"
EXPECTED="53c8398e5c408c84a323d9c1ba0d639f3c8c748142cf4ef93b275fa9cf28fbfe"
TABLES=("league_rollover_policies","league_seasons","league_rules","free_agent_publications","dead_cap_obligations","dead_cap_season_authorities","cap_season_authorities","contracts","contract_agreements","contract_seasons","contract_events","contract_transition_executions","season_roster_assignments","season_team_mappings","season_matchups","season_standings","season_playoff_brackets","historical_capture_executions","cap_adjustments","dead_cap_ledger","draft_picks","transaction_ledger","gm_user_memory","team_brain","league_brain")
def read(c,t):
    try:return c.table(t).select("*").eq("league_id",LEAGUE).execute().data or []
    except Exception:
        try:return c.table(t).select("*").execute().data or []
        except Exception:return None
def fingerprint(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()

def main():
    c=service_client();draft=CommissionerPolicyDraftService().prepare(LEAGUE,deadline=SEVEN_DAY_NOTICE_RULE,failure_to_act_outcome=RELEASE_TO_HOLD)
    independent=stable_fingerprint({k:v for k,v in draft.payload.items() if k!="fingerprint"})
    if independent!=EXPECTED:raise RuntimeError(f"Independent fingerprint mismatch: {independent}")
    before_rows={t:read(c,t) for t in TABLES};before={t:fingerprint(v) if v is not None else "unavailable" for t,v in before_rows.items()};policy_before=len(before_rows["league_rollover_policies"] or [])
    result=CommissionerPolicyApprovalService(c).approve(draft,EXPECTED)
    after_rows={t:read(c,t) for t in TABLES};after={t:fingerprint(v) if v is not None else "unavailable" for t,v in after_rows.items()};policy_after=len(after_rows["league_rollover_policies"] or [])
    changed=[t for t in TABLES if before[t]!=after[t]]
    if any(t!="league_rollover_policies" for t in changed):raise RuntimeError(f"Unexpected protected-table mutation: {changed}")
    if result.inserted and policy_after!=policy_before+1:raise RuntimeError("Approval did not add exactly one policy row")
    if not result.inserted and policy_after!=policy_before:raise RuntimeError("Idempotent approval changed policy row count")
    persisted=[x for x in after_rows["league_rollover_policies"] if x.get("id")==result.row.get("id")]
    if len(persisted)!=1:raise RuntimeError("Persisted policy verification failed")
    print(json.dumps({"approval_result":asdict(result),"independent_fingerprint":independent,"expected_fingerprint":EXPECTED,"fingerprint_match":independent==EXPECTED==result.row.get("fingerprint"),"policy_row_count_before":policy_before,"policy_row_count_after":policy_after,"changed_tables":changed,"before":before,"after":after},indent=2,sort_keys=True,default=str))
if __name__=="__main__":main()
