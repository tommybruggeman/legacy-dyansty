"""Count-only status of the live watcher: rows saved in live_stat_lines for the current NFL week, how fresh they are,
and how many player lines they hold. Prints counts only (the repo and its Actions logs are public).

    python -m scripts.watcher_status
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    from pipeline.nfl_data.common import service_client

    client = service_client()
    rows = (client.table("live_stat_lines").select("season,week,game_id,lines,source,updated_at")
            .order("updated_at", desc=True).limit(40).execute().data or [])
    if not rows:
        print("no live_stat_lines rows yet", flush=True)
        return 0
    now = datetime.now(timezone.utc)
    season, week = rows[0]["season"], rows[0]["week"]
    week_rows = [r for r in rows if (r["season"], r["week"]) == (season, week)]
    print(f"season {season} week {week}: {len(week_rows)} rows ({Counter(r['source'] for r in week_rows)})", flush=True)
    for r in week_rows:
        age = (now - datetime.fromisoformat(str(r["updated_at"]).replace("Z", "+00:00"))).total_seconds()
        kind = "heartbeat" if r["game_id"] == "_watcher" else "game"
        print(f"  {kind}: {len(r.get('lines') or {})} player lines, source {r['source']}, saved {age:.0f}s ago", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
