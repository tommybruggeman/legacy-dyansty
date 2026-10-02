"""Post the traded FAAB a Sleeper trade owes, without replaying the trade.

Usage:
    python scripts/backfill_trade_faab.py <transaction_id> [...]          # dry run
    python scripts/backfill_trade_faab.py <transaction_id> [...] --apply

Before the sync posted FAAB itself, a trade that moved budget only logged an
exception asking for the cap adjustment by hand. Replaying such a trade with
backfill_sleeper_transactions.py does not work: its players and picks already
moved, so the pick leg no longer matches its from-team and the replay stops
before the cash. This posts only the cash leg, and skips any leg already in
cap_adjustments, so running it twice is harmless.

Runs locally from the app folder: it reads SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY from .env, the same key the app uses to write
cap_adjustments. Only cap_adjustments is written, so no sync bot sign-in is
needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from scripts.run_sleeper_sync import enabled_league_ids
from services.sleeper_pipeline import SleeperSyncRunner, current_nfl_week
from services.sleeper_transaction_adapter import TradeIntent, map_transaction


def build_local_client():
    import os

    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(ROOT / ".env")
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", key)) if not v]
    if missing:
        raise SystemExit("Missing from .env: " + ", ".join(missing))
    return create_client(url, key)


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    ids = {arg.strip() for arg in argv if arg.strip() and not arg.startswith("--")}
    if not ids:
        raise SystemExit(
            "Name at least one Sleeper trade id.\n"
            "  python scripts/backfill_trade_faab.py 1410698246916984832 [--apply]"
        )

    client = build_local_client()
    write_client = read_client = client
    failed = False
    for league_id in enabled_league_ids(read_client):
        runner = SleeperSyncRunner(write_client, read_client, league_id)
        roster_map = runner.roster_map()
        sleeper_league_id = runner.sleeper_league_id()
        found = {}
        for week in range(1, current_nfl_week() + 1):
            for tx in runner._fetch(sleeper_league_id, week):
                if str(tx.get("transaction_id") or "") in ids:
                    found[str(tx["transaction_id"])] = tx

        for tx_id in sorted(ids):
            if tx_id not in found:
                print(f"[{league_id}] {tx_id}: not found in Sleeper")
                failed = True
                continue
            intents = [i for i in map_transaction(found[tx_id]) if isinstance(i, TradeIntent)]
            if not intents or not intents[0].faab_moves:
                print(f"[{league_id}] {tx_id}: not a trade that moved FAAB")
                continue
            intent = intents[0]
            missing, problems = runner.planned_faab_rows(intent, roster_map)
            for problem in problems:
                print(f"[{league_id}] {tx_id}: cannot post: {problem}")
                failed = True
            if not missing:
                print(f"[{league_id}] {tx_id}: FAAB already posted, nothing to do")
                continue
            for row in missing:
                print(f"[{league_id}] {tx_id}: {'insert' if apply else 'would insert'} "
                      f"{row['owner_name']:<18} {row['amount']:+d} season {row['season']} "
                      f"vs {row['counterparty_owner']} -- {row['note']}")
            if apply:
                problem = runner.post_faab_moves(intent, roster_map)
                if problem is not None:
                    print(f"[{league_id}] {tx_id}: {problem.detail}")
                    failed = True

    if not apply:
        print("\nDry run. Nothing was written. Re-run with --apply to post.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
