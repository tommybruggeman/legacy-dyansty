"""Why do live scores differ between our app and Sleeper right now? Player by player, for the week being played.

For every rostered player in the Legacy league it compares: our live points (the watcher's ESPN lines in
live_stat_lines, scored by our engine with our saved rules), the points Sleeper's league page shows (matchups
players_points), and Sleeper's public stat line scored with OUR rules (separates a rules gap from a stats gap).
For each gap it names the stats that differ. Prints NFL player names and numbers only: no owner or team names.

    python -m scoring.live_gaps
"""
from __future__ import annotations

import os
from typing import Any

import requests

from scoring.engine import player_points
from scoring.shadow import stat_lines

SLEEPER = "https://api.sleeper.app/v1"
LEGACY = "1361083066197491712"


def main() -> None:
    from supabase import create_client
    from league_ai.settings_store import load_settings

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    get = lambda url: requests.get(url, timeout=60).json()  # noqa: E731
    state = get(f"{SLEEPER}/state/nfl")
    season, week = int(state["season"]), int(state["week"])
    lg = (client.table("leagues").select("id").eq("sleeper_league_id", LEGACY).limit(1).execute().data or [{}])[0]
    ours_rules = {k: v for k, v in (load_settings(client, str(lg.get("id") or "")).scoring_settings or {}).items() if v}
    sl_rules = {k: v for k, v in (get(f"{SLEEPER}/league/{LEGACY}").get("scoring_settings") or {}).items() if v}
    rule_diff = sorted(k for k in set(ours_rules) | set(sl_rules) if abs(float(ours_rules.get(k) or 0) - float(sl_rules.get(k) or 0)) > 1e-9)
    print(f"season {season} week {week}; saved rules vs Sleeper's league rules differ on: {', '.join(f'{k} ours={ours_rules.get(k, 0)} sleeper={sl_rules.get(k, 0)}' for k in rule_diff) or 'nothing'}")

    rows = client.table("live_stat_lines").select("game_id,lines,updated_at").eq("season", season).eq("week", week).execute().data or []
    ours: dict[str, Any] = {}
    for r in rows:
        if r["game_id"] != "_watcher":
            ours.update(r.get("lines") or {})
    newest = max((str(r.get("updated_at")) for r in rows), default="none")
    print(f"our live rows: {len(rows)}, newest save {newest}")
    theirs = stat_lines(season, week, get_json=get)
    names = get(f"{SLEEPER}/players/nfl")
    matchups = get(f"{SLEEPER}/league/{LEGACY}/matchups/{week}") or []

    team_gaps = []
    gaps = []
    for m in matchups:
        starters = set(str(p) for p in m.get("starters") or [])
        team_ours = team_sl = 0.0
        for pid, sl_pts in (m.get("players_points") or {}).items():
            pid = str(pid)
            line = ours.get(pid)
            o = player_points(line["stats"], ours_rules, line.get("pos")) if line else None
            t = theirs.get(pid)
            s_ours_rules = player_points(t["stats"], ours_rules, t.get("pos")) if t else 0.0
            if pid in starters:
                team_ours += o if o is not None else float(sl_pts or 0)
                team_sl += float(sl_pts or 0)
            if o is None or abs(o - float(sl_pts or 0)) < 0.05:
                continue
            a, b = (line or {}).get("stats") or {}, (t or {}).get("stats") or {}
            keys = [k for k in ours_rules if abs(float(a.get(k) or 0) - float(b.get(k) or 0)) > 1e-6]
            p = names.get(pid) or {}
            gaps.append((abs(o - float(sl_pts or 0)), f"{p.get('full_name') or pid} ({p.get('position')}, {p.get('team')}){' starter' if pid in starters else ''}: "
                         f"ours {o:.2f}, Sleeper page {float(sl_pts or 0):.2f}, Sleeper stats w/ our rules {s_ours_rules:.2f}; "
                         f"stats that differ: " + (", ".join(f"{k} ours={a.get(k, 0)} sleeper={b.get(k, 0)}" for k in keys) or "none (rules or timing)")))
        team_gaps.append(round(team_ours - team_sl, 2))
    print(f"team total gaps (ours minus Sleeper), per team: {sorted(team_gaps)}")
    print(f"{len(gaps)} rostered players differ:")
    for _, text in sorted(gaps, reverse=True):
        print("  " + text)


if __name__ == "__main__":
    main()
