from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth import service_client
from season_engine.history import PreRolloverHistoryService


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or atomically capture pre-rollover season history.")
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Call the atomic capture RPC; default is zero-write dry run.")
    args = parser.parse_args()
    result = PreRolloverHistoryService(service_client()).capture(args.league_id, dry_run=not args.apply)
    plan = result.plan
    report = {
        "dry_run": result.dry_run, "applied": result.applied, "safe_to_apply": plan.safe_to_apply,
        "league_id": plan.league_id, "league_season_id": plan.league_season_id,
        "season": plan.season, "sleeper_league_id": plan.sleeper_league_id,
        "source_fingerprint": plan.source_fingerprint, "idempotency_key": plan.idempotency_key,
        "expected_counts": plan.expected_counts, "existing_counts": plan.existing_counts,
        "warnings": plan.warnings, "blocking_errors": plan.blocking_errors,
        "database_result": result.database_result,
    }
    print(json.dumps(report, indent=2))
    return 0 if plan.safe_to_apply else 2


if __name__ == "__main__": raise SystemExit(main())
