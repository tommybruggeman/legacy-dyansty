from __future__ import annotations

import argparse,json
from typing import Any

from .transition_planner import build_transition_plan
from .transition_validator import validate_contract_transition


class ContractTransitionService:
    """Read-only transition planning. This class has no insert/update/delete/RPC path."""
    def __init__(self,client:Any):self.client=client
    def plan(self,league_id:str,source_season:int,target_season:int,expected_source_fingerprint:str|None=None):
        def read(table,league_filter=True):
            query=self.client.table(table).select("*")
            if league_filter:query=query.eq("league_id",league_id)
            return query.execute().data or []
        seasons=read("league_seasons")
        active=next((x for x in seasons if int(x["season"])==source_season),None)
        roster=(self.client.table("season_roster_assignments").select("*").eq("league_season_id",active["id"]).execute().data or []) if active else []
        history={t:(self.client.table(t).select("*").eq("league_season_id",active["id"]).execute().data or []) if active else []
            for t in ("season_team_mappings","season_matchups","season_standings","season_playoff_brackets")}
        return build_transition_plan(league_id=league_id,source_season=source_season,target_season=target_season,
            league_seasons=seasons,legacy_contracts=read("contracts"),agreements=read("contract_agreements"),
            contract_seasons=read("contract_seasons"),events=read("contract_events"),teams=read("league_teams"),
            roster_assignments=roster,cap_adjustments=read("cap_adjustments",False),dead_cap=read("dead_cap_ledger",False),
            free_agent_source=read("player_universe",False),draft_picks=read("draft_picks",False),historical_facts=history,
            expected_source_fingerprint=expected_source_fingerprint)


def plan_contract_transition(client,league_id,source_season,target_season,expected_source_fingerprint=None):return ContractTransitionService(client).plan(league_id,source_season,target_season,expected_source_fingerprint)
def get_expiring_contracts_for_transition(plan):return [x for x in plan.classifications if x["outcome"]=="EXPIRES_AFTER_2025"]
def get_continuing_contracts_for_transition(plan):return [x for x in plan.classifications if x["outcome"]=="CONTINUES"]
def get_team_transition_summary(plan,league_team_id=None):return [x for x in plan.team_projections if league_team_id is None or x["league_team_id"]==league_team_id]
def get_free_agent_candidates_for_transition(plan):return list(plan.free_agent_candidates)
def get_transition_source_fingerprint(plan):return plan.source_fingerprint


def main()->int:
    parser=argparse.ArgumentParser(); parser.add_argument("command",choices=["dry-run"]); parser.add_argument("--league-id",required=True)
    parser.add_argument("--source-season",type=int,required=True); parser.add_argument("--target-season",type=int,required=True); parser.add_argument("--json",action="store_true")
    args=parser.parse_args()
    from auth import service_client
    plan=ContractTransitionService(service_client()).plan(args.league_id,args.source_season,args.target_season)
    report={"safe_to_transition":plan.safe_to_transition,"counts":plan.counts,"source_fingerprint":plan.source_fingerprint,"plan_fingerprint":plan.plan_fingerprint,
        "team_projections":plan.team_projections,"free_agent_candidates":plan.free_agent_candidates,"warnings":plan.warnings,"blocking_errors":plan.blocking_errors}
    if args.json:
        print(json.dumps(report,indent=2,default=str))
    else:
        print(f"safe_to_transition: {plan.safe_to_transition}")
        print(f"source_fingerprint: {plan.source_fingerprint}")
        print(f"plan_fingerprint: {plan.plan_fingerprint}")
        for key,value in plan.counts.items():print(f"{key}: {value}")
        print(f"warnings: {len(plan.warnings)}")
        print(f"blocking_errors: {len(plan.blocking_errors)}")
    return 0 if plan.safe_to_transition else 2


if __name__=="__main__":raise SystemExit(main())
