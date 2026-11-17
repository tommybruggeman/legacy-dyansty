"""Read-only check of our own player status feed (api.player_status: ESPN rosters + injury report) against what the
app shows today (Sleeper's nightly status in nfl_players). Saves nothing. Prints counts only (public Actions logs).

    python -m scripts.status_probe
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import player_status as ps  # noqa: E402
from pipeline.nfl_data.common import fetch_json  # noqa: E402


def main() -> int:
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    t = time.monotonic()
    entries = ps.fetch_feed(lambda url: fetch_json(url, timeout=30))
    if entries is None:
        print("feed down")
        return 1
    print(f"feed: {len(entries)} players in {time.monotonic() - t:.1f}s")
    print("our labels:", dict(Counter(e["status"] for e in entries.values()).most_common()))
    t = time.monotonic()
    ids = ps.match_ids(client, entries, {})
    print(f"matched to our players: {len(ids)} of {len(entries)} in {time.monotonic() - t:.1f}s")
    ours = {sid: entries[e]["status"] for e, sid in ids.items()}
    sleeper: dict[str, str] = {}
    for r in ps._all(client, "nfl_players", "sleeper_id,injury_status,position", "sleeper_id"):
        if r.get("injury_status") and r.get("position") in ps.POSITIONS:
            sleeper[str(r["sleeper_id"])] = ps.label(r["injury_status"])
    sleeper = {k: v for k, v in sleeper.items() if v != ps.HEALTHY}
    both = {s for s in set(ours) | set(sleeper) if ours.get(s, ps.HEALTHY) != ps.HEALTHY or s in sleeper}
    pairs = Counter((sleeper.get(s, ps.HEALTHY), ours.get(s, ps.HEALTHY)) for s in both)
    same = sum(n for (a, b), n in pairs.items() if a == b)
    print(f"players hurt in either: {len(both)}; same status: {same} ({same / max(len(both), 1):.0%})")
    print("app today (Sleeper nightly) -> ours now:")
    for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        print(f"  {a:>12} -> {b:<12} {n}{'' if a != b else '  (same)'}")
    off = [s for s in sleeper if s not in ours]
    print(f"hurt in the app but on no ESPN roster (cut, retired, or not matched): {len(off)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
