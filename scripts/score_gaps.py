"""Why our scores differ from Sleeper's, stat by stat. For each week of the 2026 league (default: last 2 weeks),
every rostered starter whose points differ: which stat keys differ between our line and Sleeper's public line, how
many players and how many points each key explains, and which source each gap comes from (official finals vs live
watcher vs no line). Prints counts and stat keys only (the repo and its Actions logs are public). Saves nothing.

    python -m scripts.score_gaps [weeks...]
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.own_scores_check import _stub_streamlit  # noqa: E402


def main(argv: list[str]) -> int:
    _stub_streamlit()
    from api import league, live_stats, own_league, sleeper
    from league_ai.settings_store import load_settings
    from pipeline.nfl_data.common import service_client
    from scoring import engine
    from scoring.shadow import stat_lines

    client = service_client()
    current = league.current_week()
    seasons = (client.table("league_seasons").select("league_id,season,sleeper_league_id")
               .eq("is_active", True).eq("season", 2026).execute().data or [])
    weeks = [int(a) for a in argv] or [current - 1, current]
    for s in seasons:
        sid = s["sleeper_league_id"]
        scoring = load_settings(client, s["league_id"]).scoring_settings or (sleeper.raw(f"league/{sid}") or {}).get("scoring_settings") or {}
        for week in weeks:
            ours = own_league.week_lines(client, 2026, week, current)
            official = live_stats.final_lines(client, 2026, week) if week < current else {}
            live = live_stats.fresh_lines(client, 2026, week) if week == current else {}
            theirs = stat_lines(2026, week)
            rows = sleeper.raw(f"league/{sid}/matchups/{week}") or []
            keys_n, keys_pts, kinds = Counter(), defaultdict(float), Counter()
            n_diff, total = 0, 0.0
            starters = {str(p) for r in rows for p in (r.get("starters") or []) if p and str(p) != "0"}
            sleeper_pts = {str(p): float(v or 0) for r in rows for p, v in (r.get("players_points") or {}).items()}
            for pid in starters:
                line = ours.get(pid)
                mine = engine.player_points(line.get("stats"), scoring, line.get("pos")) if line else sleeper_pts.get(pid, 0)
                gap = round(mine - sleeper_pts.get(pid, 0), 2)
                if abs(gap) < 0.005:
                    continue
                n_diff += 1
                total += gap
                src = "official" if pid in official else "live" if pid in live else "sleeper-stats" if pid in theirs else "none"
                pos = (line or {}).get("pos") or (theirs.get(pid) or {}).get("pos") or "?"
                kinds[f"{src}/{pos}"] += 1
                a = engine.full_line((line or {}).get("stats"), pos, scoring)
                b = engine.full_line((theirs.get(pid) or {}).get("stats"), pos, scoring)
                explained = False
                for key, w in scoring.items():
                    try:
                        d = (float(a.get(key) or 0) - float(b.get(key) or 0)) * float(w or 0)
                    except (TypeError, ValueError):
                        continue
                    if abs(d) >= 0.005:
                        keys_n[key] += 1
                        keys_pts[key] += d
                        explained = True
                if not explained:
                    keys_n["(same stats, Sleeper's points differ: stat correction or adjustment)"] += 1
            print(f"week {week}: {n_diff} of {len(starters)} starters differ, net {total:+.2f} pts; by source/pos {dict(kinds)}", flush=True)
            for key, n in keys_n.most_common():
                print(f"  {key}: {n} players, {keys_pts.get(key, 0):+.2f} pts (ours minus Sleeper's line)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
