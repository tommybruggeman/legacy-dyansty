"""Practice-day probe (League AI checks > practice_probe): what the day-by-day practice table holds this week.

Prints counts per day and source, rows missing the Sleeper id the player page reads by, and one sample player's
lines (an NFL player, public data; default Mike Evans) plus what the player page builds from them. No league data.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.nfl_data.common import service_client  # noqa: E402
from pipeline.nfl_data.run import current_nfl_season  # noqa: E402


def main() -> int:
    client = service_client()
    season = current_nfl_season()
    gsis = os.environ.get("PROBE_GSIS", "").strip()
    gsis = gsis if gsis.startswith("00-") else "00-0031408"
    rows = (client.table("nfl_practice_days").select("week").eq("season", season).order("week", desc=True).limit(1).execute().data or [])
    if not rows:
        print("no practice days this season")
        return 0
    week = int(rows[0]["week"])
    data = (client.table("nfl_practice_days").select("practice_date,day,source,practice_status,game_status,sleeper_id,refreshed_at")
            .eq("season", season).eq("week", week).limit(5000).execute().data or [])
    print(f"season {season} week {week}: {len(data)} rows")
    for (d, day, src), n in sorted(Counter((r["practice_date"], r["day"], r["source"]) for r in data).items()):
        print(f"  {d} {day} {src}: {n}")
    print("missing sleeper_id by source:", dict(Counter(r["source"] for r in data if not r.get("sleeper_id"))))
    mine = (client.table("nfl_practice_days").select("week,practice_date,day,source,practice_status,game_status,sleeper_id,refreshed_at")
            .eq("gsis_id", gsis).eq("season", season).order("practice_date").limit(40).execute().data or [])
    print(f"sample player {gsis}:")
    for r in mine:
        print("  ", {k: r.get(k) for k in ("week", "practice_date", "day", "source", "practice_status", "game_status", "refreshed_at")},
              "sleeper_id set" if r.get("sleeper_id") else "NO sleeper_id")
    sid = next((r["sleeper_id"] for r in mine if r.get("sleeper_id")), None)
    if sid:
        espn = (client.table("nfl_injuries").select("status,reported_at,short_comment,long_comment").eq("sleeper_id", sid).limit(1).execute().data or [])
        print("ESPN note:", espn[0] if espn else None)
    last = (client.table("nfl_practice_days").select("refreshed_at").order("refreshed_at", desc=True).limit(1).execute().data or [])
    print("newest practice write:", last[0]["refreshed_at"] if last else None)
    if sid:
        from api.player import health, load_practice, load_practice_days

        h = health({"injury_status": "Questionable"}, None, load_practice(client, sid), [], season, None, load_practice_days(client, sid, season))
        print("player page this_week:", h.get("this_week"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
