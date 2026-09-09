"""Scheduled entrypoint: apply Sleeper transactions for every enabled league.

Authenticates as the dedicated sync bot rather than using a service key. The
contract RPCs are granted to `authenticated` only and call
require_commissioner_authority, so the bot must be a real Supabase user holding
commissioner role in league_memberships.

Environment:
    SUPABASE_URL, SUPABASE_ANON_KEY, SYNC_BOT_EMAIL, SYNC_BOT_PASSWORD
    LEGACY_LEAGUE_ID    optional; restrict the run to one league
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from supabase import create_client

from services.sleeper_pipeline import SleeperSyncRunner


def _required(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def build_client():
    client = create_client(_required("SUPABASE_URL"), _required("SUPABASE_ANON_KEY"))
    session = client.auth.sign_in_with_password({
        "email": _required("SYNC_BOT_EMAIL"),
        "password": _required("SYNC_BOT_PASSWORD"),
    })
    if not getattr(session, "session", None):
        raise SystemExit("Sync bot sign-in failed; check the credentials.")
    return client


def enabled_league_ids(client) -> list[str]:
    scoped = (os.getenv("LEGACY_LEAGUE_ID") or "").strip()
    query = client.table("league_sleeper_sync").select("league_id,sync_enabled")
    if scoped:
        query = query.eq("league_id", scoped)
    rows = query.execute().data or []
    return [
        str(row["league_id"]) for row in rows
        if row.get("sync_enabled") and row.get("league_id")
    ]


def main() -> int:
    client = build_client()
    league_ids = enabled_league_ids(client)

    if not league_ids:
        print("No leagues have the Sleeper sync enabled. Nothing to do.")
        return 0

    failed = False
    for league_id in league_ids:
        # The bot reads and writes as itself, so RLS applies to both.
        runner = SleeperSyncRunner(client, client, league_id)
        try:
            report = runner.run()
        except Exception as exc:
            failed = True
            print(f"[{league_id}] sync failed: {exc}")
            continue

        print(f"[{league_id}] {report.summary()}")
        for exception in report.exceptions:
            print(f"[{league_id}]   flagged {exception.kind}: {exception.detail}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
