"""Apply named Sleeper transactions that the sync never picked up.

Usage:
    python scripts/backfill_sleeper_transactions.py <transaction_id> [...]

Use this, never a watermark rewind, to repair a gap the sync has already
passed. A rewind replays everything in between, including adds whose player
was later dropped by hand -- and an add that finds no live contract writes a
new one, putting a released player back under contract. This applies only the
transactions named and leaves the watermark untouched.

Find the ids with scripts/reconcile_sleeper_transactions.py, which prints one
per row, then verify with the same script afterwards.

Environment: the same five secrets the scheduled sync uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from scripts.run_sleeper_sync import build_clients, enabled_league_ids, preflight
from services.sleeper_pipeline import SleeperSyncRunner


def main(argv: list[str]) -> int:
    ids = [arg.strip() for arg in argv if arg.strip()]
    if not ids:
        raise SystemExit(
            "Name at least one Sleeper transaction id.\n"
            "  python scripts/backfill_sleeper_transactions.py 1401109608193400832 ..."
        )

    preflight()
    write_client, read_client = build_clients()
    league_ids = enabled_league_ids(read_client)
    if not league_ids:
        print("No leagues have the Sleeper sync enabled. Nothing to do.")
        return 0

    print(f"\nBackfilling {len(ids)} transaction(s): {', '.join(ids)}")
    failed = False
    for league_id in league_ids:
        runner = SleeperSyncRunner(write_client, read_client, league_id)
        try:
            report = runner.apply_specific(ids)
        except Exception as exc:
            failed = True
            print(f"[{league_id}] backfill failed: {exc}")
            continue

        print(f"[{league_id}] {report.summary()}")
        for exception in report.exceptions:
            where = f"transaction {exception.transaction_id}"
            if exception.player_id:
                where = f"player {exception.player_id}, roster {exception.roster_id}, " + where
            print(f"[{league_id}]   flagged {exception.kind} ({where}): {exception.detail}")
            failed = True

    print("\nWatermark untouched. Re-run scripts/reconcile_sleeper_transactions.py to verify.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
