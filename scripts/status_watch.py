"""The status watcher: always on, on the free watcher VM. Once a minute it reads ESPN's injury report (the 32 team
rosters every 5 minutes) into our own player status (api.player_status). When a player's status changes it alerts
his owner straight away: the status alert, and for a starter ruled out before his game the injury alert with the
swap (scripts/push_alerts.py producers), then sends them (api.alerts.deliver). The 5-minute alert job does
everything else.

A worse status (Questionable, Out, IR...) counts the moment it shows; a change toward healthy waits one more read.
Touches /run/legacy-status.beat each check so the alert job knows not to read statuses itself.
Prints counts only (public repo).

Environment: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, APNS_KEY_P8, APNS_KEY_ID; INTERVAL (seconds, default 60).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import alerts, apns, player_status  # noqa: E402
from pipeline.nfl_data.common import fetch_json  # noqa: E402
from scripts import push_alerts as pa  # noqa: E402

INTERVAL = int(os.environ.get("INTERVAL", "60"))


def beat() -> None:
    try:
        with open(pa.STATUS_BEAT, "a"):
            os.utime(pa.STATUS_BEAT, None)
    except OSError:
        pass


def check(client, cache: dict) -> int:
    now = datetime.now(timezone.utc)
    pa.CLIENT[:] = [client]
    pa.CHANGES[:] = player_status.refresh(client, lambda url: fetch_json(url, timeout=20), now, cache)
    beat()
    if not pa.CHANGES:
        return 0
    pa.run_leagues(client, now, [("game day", pa.game_day), ("status", pa.status_alerts)])
    if apns.configured():
        push = apns.Client.from_env()
        try:
            print(f"delivery: {alerts.deliver(client, push, now)}", flush=True)
        finally:
            push.close()
    return len(pa.CHANGES)


def main() -> int:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    cache: dict = {}
    while True:
        started = time.monotonic()
        try:
            check(client, cache)
        except Exception as exc:  # keep watching; the next minute tries again
            print(f"status check failed ({type(exc).__name__})", flush=True)
        time.sleep(max(5.0, INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
