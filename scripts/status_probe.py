"""Read-only check of our own player status feed (api.player_status) against what the app shows today (Sleeper's
nightly status in nfl_players). Saves nothing. Prints counts only (public Actions logs).

    python -m scripts.status_probe
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import player_status as ps  # noqa: E402
from pipeline.nfl_data.common import fetch_json  # noqa: E402


def main() -> int:
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    raw_payload = None
    for url in ps.ESPN_INJURIES_URLS:
        try:
            raw_payload = fetch_json(url, timeout=30)
            break
        except Exception as exc:
            print(f"feed {url.split('/')[2]}: {type(exc).__name__}")
    if not raw_payload:
        print("no feed")
        return 1
    raw = Counter()
    fantasy = Counter()
    for team in raw_payload.get("injuries") or []:
        for e in team.get("injuries") or []:
            raw[str(e.get("status"))] += 1
            fs = (e.get("details") or {}).get("fantasyStatus") or {}
            fantasy[f"{fs.get('abbreviation')}|{fs.get('description')}"] += 1
    print("ESPN raw status words (all positions):", dict(raw.most_common()))
    print("ESPN fantasyStatus (abbr|description):", dict(fantasy.most_common(15)))
    entries = ps.espn_entries(raw_payload)
    print("our labels (fantasy positions):", dict(Counter(e["status"] for e in entries.values()).most_common()))
    ids = ps.match_ids(client, entries, {})
    print(f"matched to our players: {len(ids)} of {len(entries)}")
    ours = {sid: entries[e]["status"] for e, sid in ids.items()}
    sleeper: dict[str, str] = {}
    start = 0
    while True:
        rows = (client.table("nfl_players").select("sleeper_id,injury_status,position").not_.is_("injury_status", "null")
                .order("sleeper_id").range(start, start + 999).execute().data or [])
        sleeper.update({str(r["sleeper_id"]): ps.label(r.get("injury_status")) for r in rows if r.get("position") in ps.POSITIONS})
        if len(rows) < 1000:
            break
        start += 1000
    sleeper = {k: v for k, v in sleeper.items() if v != ps.HEALTHY}
    both = set(ours) | set(sleeper)
    pairs = Counter((sleeper.get(s, ps.HEALTHY), ours.get(s, ps.HEALTHY)) for s in both)
    same = sum(n for (a, b), n in pairs.items() if a == b)
    print(f"players hurt in either: {len(both)}; same status: {same} ({same / max(len(both), 1):.0%})")
    print("app today (Sleeper nightly) -> ESPN now, where they differ:")
    for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        if a != b:
            print(f"  {a:>12} -> {b:<12} {n}")
    rosters(client, sleeper, ids)
    return 0


def rosters(client, sleeper, ids) -> None:
    """ESPN's 32 team rosters: what status fields an athlete carries, and whether they cover the IR/PUP players."""
    from pipeline.nfl_data import espn_rosters as er
    team_ids = er.espn_team_ids(fetch_json(er.ESPN_TEAMS_URL, timeout=60))
    keys, st_type, st_name, inj = Counter(), Counter(), Counter(), Counter()
    by_espn = {}
    for tid in team_ids:
        try:
            payload = fetch_json(er.ESPN_ROSTER_URL.format(team_id=tid), timeout=60)
        except Exception:
            continue
        for group in payload.get("athletes") or []:
            for a in (group.get("items") if isinstance(group, dict) and "items" in group else [group]) or []:
                pos = ((a.get("position") or {}).get("abbreviation"))
                if pos not in ps.POSITIONS:
                    continue
                keys.update(k for k in a.keys())
                st = a.get("status") or {}
                st_type[str(st.get("type"))] += 1
                st_name[str(st.get("name") or st.get("abbreviation"))] += 1
                injs = a.get("injuries") or []
                inj[str((injs[0] or {}).get("status")) if injs else "none"] += 1
                by_espn[str(a.get("id"))] = (str(st.get("type")), str((injs[0] or {}).get("status")) if injs else "")
    print(f"rosters: {len(team_ids)} teams; athlete keys: {sorted(k for k, n in keys.items() if n > 50)}")
    print("roster status.type:", dict(st_type.most_common()))
    print("roster status.name:", dict(st_name.most_common()))
    print("roster injuries[0].status:", dict(inj.most_common()))
    rev = {}
    for r in client.table("nfl_injuries").select("espn_id,sleeper_id").execute().data or []:
        if r.get("sleeper_id"):
            rev[str(r["sleeper_id"])] = str(r["espn_id"])
    rev.update({s: e for e, s in ids.items()})
    cover = Counter()
    for sid, lab in sleeper.items():
        if lab in ("IR", "PUP", "Out", "Questionable"):
            e = rev.get(sid)
            cover[(lab, by_espn.get(e, ("not found", ""))[0] if e else "no espn id")] += 1
    print("app today (Sleeper) vs ESPN roster status.type:")
    for (a, b), n in sorted(cover.items()):
        print(f"  {a:>12} | {b:<24} {n}")


if __name__ == "__main__":
    sys.exit(main())
