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

from services.sleeper_pipeline import SleeperSyncRunner


REQUIRED_ENV = (
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SYNC_BOT_EMAIL",
    "SYNC_BOT_PASSWORD",
    # Canonical tables are not readable by the `authenticated` role -- all
    # access is meant to go through SECURITY DEFINER functions. Reads therefore
    # use the service role, exactly as the Streamlit app does. Writes still go
    # through the bot's own session so commissioner authority is enforced.
    "SUPABASE_SERVICE_ROLE_KEY",
)


def preflight() -> None:
    """Report every missing secret at once, not just the first.

    Never prints a value -- only whether the name resolved to something.
    """
    missing = [name for name in REQUIRED_ENV if not (os.getenv(name) or "").strip()]
    print("Sleeper sync preflight:")
    for name in REQUIRED_ENV:
        print(f"  {'set  ' if name not in missing else 'MISSING'}  {name}")
    if missing:
        raise SystemExit(
            "\nCannot run. Add these as repository secrets under "
            "Settings -> Secrets and variables -> Actions: " + ", ".join(missing)
        )


def _required(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def build_clients():
    """Return (write_client, read_client).

    write_client is the sync bot's authenticated session, so every contract RPC
    runs under require_commissioner_authority. read_client uses the service role
    purely to read canonical tables the authenticated role cannot select from.
    """
    # Imported here so a missing secret reports before a missing package.
    from supabase import create_client

    url = _required("SUPABASE_URL")
    write_client = create_client(url, _required("SUPABASE_ANON_KEY"))
    session = write_client.auth.sign_in_with_password({
        "email": _required("SYNC_BOT_EMAIL"),
        "password": _required("SYNC_BOT_PASSWORD"),
    })
    if not getattr(session, "session", None):
        raise SystemExit("Sync bot sign-in failed; check the credentials.")

    read_client = create_client(url, _required("SUPABASE_SERVICE_ROLE_KEY"))
    return write_client, read_client


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
    preflight()
    write_client, read_client = build_clients()
    league_ids = enabled_league_ids(read_client)

    if not league_ids:
        print("No leagues have the Sleeper sync enabled. Nothing to do.")
        return 0

    failed = False
    for league_id in league_ids:
        runner = SleeperSyncRunner(write_client, read_client, league_id)
        try:
            report = runner.run()
        except Exception as exc:
            failed = True
            print(f"[{league_id}] sync failed: {exc}")
            continue

        print(f"[{league_id}] {report.summary()}")
        for exception in report.exceptions:
            # Name the player and transaction: a bare message leaves the
            # commissioner guessing which move was left unapplied.
            where = f"transaction {exception.transaction_id}"
            if exception.player_id:
                where = f"player {exception.player_id}, roster {exception.roster_id}, " + where
            print(f"[{league_id}]   flagged {exception.kind} ({where}): {exception.detail}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
