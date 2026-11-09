"""Why a player's projection is what it is: Sleeper's line, our matchup factor, calibration and nudge, step by step.

Prints NFL player names and numbers only (no owners). Usage: python -m scripts.projection_trace "E. Wilson" "D. Vele"
"""
from __future__ import annotations

import sys

from scripts.own_scores_check import _stub_streamlit


def main(names: list[str]) -> int:
    _stub_streamlit()
    from api import league, projections
    from league_ai.nfl_data import defense_vs_position, season_games, team_code, total_multiplier, week_matchups
    from pipeline.nfl_data.common import service_client

    client = service_client()
    season_row = next(iter(client.table("league_seasons").select("season,sleeper_league_id").eq("is_active", True)
                           .order("season", desc=True).limit(1).execute().data or []), None)
    if not season_row:
        print("no active season")
        return 1
    season, sid, week = int(season_row["season"]), str(season_row["sleeper_league_id"]), league.current_week()
    lines = projections.week_lines("projections", season, week, 60)
    stats_prev = {w: projections.week_lines("stats", season, w, 60) for w in range(max(1, week - 3), week)}
    scoring = projections.league_scoring(sid)
    games = week_matchups(season_games(client, season), week)
    dvp = defense_vs_position(client, season)
    players = league.sleeper.get("players/nfl", ttl=86400) or {}
    print(f"season {season} week {week}", flush=True)
    for name in names:
        first, _, last = name.replace(".", "").partition(" ")
        hits = [(pid, p) for pid, p in players.items() if str(p.get("last_name") or "").lower() == last.strip().lower()
                and str(p.get("first_name") or "").lower().startswith(first.strip().lower()) and p.get("position") in ("QB", "RB", "WR", "TE")]
        for pid, p in hits:
            line = lines.get(pid) or {}
            pos, team = p.get("position"), team_code(p.get("team"))
            stats = line.get("stats") or {}
            base = projections.score(stats, scoring, pos) if stats else None
            game = games.get(team)
            factor = total_multiplier(pos, game, dvp.get((game["opponent"], pos)) if game else None, conditions=True) if game else None
            final = projections.week_projections(client, sid, season, week, [pid], {pid: pos}, {pid: p.get("team")})
            keys = {k: stats.get(k) for k in ("pts_ppr", "rush_att", "rush_yd", "rec_tgt", "rec", "rec_yd", "gp") if k in stats}
            recent = [round(projections.score(s[pid]["stats"], scoring, pos), 1) if pid in s and projections.played(s[pid]["stats"]) else None for s in stats_prev.values()]
            print(f"{p.get('full_name')} {pos} {p.get('team')} status={p.get('injury_status')} depth={p.get('depth_chart_order')}"
                  f" | sleeper line {keys} -> league pts {base} | matchup factor {factor} | final {final.get(pid)} | last weeks {recent}", flush=True)
        if not hits:
            print(f"{name}: not found", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["E. Wilson"]))
