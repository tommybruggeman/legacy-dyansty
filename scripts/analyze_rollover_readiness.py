"""Read-only Phase 3B.4D production analysis; performs SELECT operations only."""
from __future__ import annotations

from dataclasses import asdict
from collections import Counter
import argparse
import hashlib
import json

from auth import service_client
from season_engine.rollover_service import RolloverAuthorityService


PROTECTED_TABLES=(
    "league_seasons","contracts","contract_agreements","contract_seasons","contract_events",
    "contract_transition_executions","season_roster_assignments","league_teams","cap_adjustments",
    "dead_cap_ledger","free_agents","waiver_claims","draft_picks","transaction_ledger",
    "gm_user_memory","gm_team_brain","gm_league_brain","player_strategic_state",
    "season_team_mappings","season_matchups","season_standings","season_playoff_brackets",
)


def _fingerprints(client,league_id):
    result={}
    for table in PROTECTED_TABLES:
        try:
            rows=client.table(table).select("*").eq("league_id",league_id).execute().data or []
            result[table]=hashlib.sha256(json.dumps(rows,sort_keys=True,default=str,separators=(",",":")).encode()).hexdigest()
        except Exception as exc:
            result[table]="unavailable:"+type(exc).__name__
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--summary",action="store_true");args=parser.parse_args()
    client=service_client()
    leagues=client.table("league_seasons").select("league_id").eq("is_active",True).execute().data or []
    league_ids=sorted({str(row["league_id"]) for row in leagues})
    if len(league_ids)!=1:
        raise RuntimeError(f"Expected exactly one development league with active season; found {league_ids}")
    before=_fingerprints(client,league_ids[0])
    report=RolloverAuthorityService(client).build_rollover_readiness_report(league_ids[0])
    after=_fingerprints(client,league_ids[0])
    if args.summary:
        groups={}
        for row in report.roster_exceptions:groups.setdefault(row.classification,[]).append({"player":row.player_name,"player_id":row.player_id,"team_id":row.team_id,"roster_status":row.roster_status,"publication":row.publication_status,"proposed_action":row.proposed_action})
        output={"authority":{"league":report.context.source_league_season,"contract":report.context.contract_operational_season,"cap":report.context.cap_authority_season,"target":report.context.target_league_season},"overall":report.context.readiness_status,"projected_caps":[asdict(x) for x in report.projected_caps],"exception_counts":dict(Counter(x.classification for x in report.roster_exceptions)),"exceptions":groups,"commissioner_decisions":len(report.commissioner_decisions),"publication_authority":report.publication_authority,"blockers":report.blockers,"warnings":report.warnings,"source_fingerprint":report.source_fingerprint,"plan_fingerprint":report.plan_fingerprint,"protected_before":before,"protected_after":after,"protected_changed":[table for table in before if before[table]!=after[table]],"writes_performed":report.writes_performed}
    else:output=asdict(report)
    print(json.dumps(output,indent=2,sort_keys=True,default=str))


if __name__=="__main__":main()
