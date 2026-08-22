from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from auth import service_client
from contract_engine import ContractBackfillService


def main() -> int:
    parser=argparse.ArgumentParser(description="Plan or atomically apply the Phase 3A normalized contract backfill.")
    parser.add_argument("--league-id",required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    service=ContractBackfillService(service_client())
    if args.apply:
        result=service.backfill(args.league_id,dry_run=False)
        print(json.dumps({"applied":True,"database_result":result},indent=2,default=str)); return 0
    plan=service.backfill(args.league_id,dry_run=True)
    print(json.dumps({"applied":False,"safe_to_apply":plan.safe_to_apply,"source_contract_count":plan.source_contract_count,
        "source_fingerprint":plan.source_fingerprint,"counts":plan.counts,"future_league_seasons":plan.future_league_seasons,
        "warnings":plan.warnings,"blocking_errors":plan.blocking_errors},indent=2,default=str))
    return 0 if plan.safe_to_apply else 2


if __name__=="__main__": raise SystemExit(main())
