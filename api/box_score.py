"""One game's box score with each starter's projection, game status, weekly position rank and stat breakdown.

Shared by the app server (/matchups/{week}/{matchup_id}) and the website's Weekly Matchups page, so both show the same numbers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from api import cache, home, league, nfl_live, projections, teams
from api.front_office import LEAGUE_TZ

PROJ_TTL = 900
BENCH_ORDER = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DEF": 5}


def game(client: Any, ctx: dict[str, Any], week: int, matchup_id: int, team_rows: Iterable[dict[str, Any]] = ()) -> dict[str, Any] | None:
    """ctx needs season, sleeper_id, names (roster id -> owner), slots and sleeper_roster_id. None when there's no such game."""
    final = week <= league.last_scored_week(ctx["sleeper_id"])
    ttl = 86400 if final else league.LIVE_TTL
    ids = teams.matchup_player_ids(ctx["sleeper_id"], week, matchup_id, ttl)
    info = teams.player_info(client, ids)
    result = teams.box_score(ctx["sleeper_id"], week, matchup_id, ctx["names"], ctx["slots"], info, ttl)
    if result is None:
        return None
    teams_by_roster = {t["roster_id"]: t["team_id"] for t in team_rows}
    for side in result["teams"]:
        side["team_id"] = teams_by_roster.get(side["roster_id"])
    result["my_roster_id"] = ctx.get("sleeper_roster_id")
    result["is_final"] = final
    add_projections(client, ctx, week, final, result, info)
    result["win_chance"] = nfl_live.win_chance(result["teams"], final)
    return result


def week_kickoffs(client: Any, season: int, week: int) -> dict[str, datetime]:
    try:
        nfl = cache.cached(("nfl_games", season, week), 600, lambda: client.table("nfl_games").select("gameday,gametime,home_team,away_team")
                           .eq("season", season).eq("week", week).execute().data or [])
    except Exception:
        nfl = []
    return home.kickoffs(nfl)


def add_projections(client: Any, ctx: dict[str, Any], week: int, final: bool, result: dict[str, Any], info: dict[str, dict[str, Any]]) -> None:
    """Each starter's and bench player's projection, game status and weekly position rank, plus each side's
    projected total (starters only). The bench is sorted QB, RB, WR, TE, then by projection.

    A side's projection counts real points for finished games, points so far plus the
    projection for the game time still left for games in progress, and the projection otherwise.
    """
    ids = [p["player_id"] for side in result["teams"] for p in side["players"] + side.get("bench", []) if p["player_id"] not in ("", "0")]
    positions = {pid: str(info.get(pid, {}).get("position") or "") for pid in ids}
    nfl_teams = {pid: str(info.get(pid, {}).get("team") or "") for pid in ids}
    season, sid = ctx["season"], ctx["sleeper_id"]
    proj_key = ("proj", sid, season, week)
    proj_job = cache.pool.submit(lambda: cache.cached(proj_key, PROJ_TTL, lambda: projections.week_projections(client, sid, season, week, ids, positions, nfl_teams)))
    rank_job = cache.pool.submit(lambda: projections.week_ranks(sid, season, week, final, positions))
    # week_ranks reads the same stat lines, so this second read comes from the Sleeper cache.
    lines_job = cache.pool.submit(lambda: projections.week_lines("stats", season, week, 86400 if final else 120))
    scoring_job = cache.pool.submit(lambda: projections.league_scoring(sid))
    games_job = cache.pool.submit(lambda: nfl_live.week_games(season, week, final))
    starts = week_kickoffs(client, season, week)
    now = datetime.now(LEAGUE_TZ)
    try:
        proj = proj_job.result()
    except Exception:
        proj = {}
    missing = set(ids) - set(proj)
    if missing:  # cached projections were for other players (another game, the bench): score these and keep them all
        try:
            proj = {**proj, **{pid: None for pid in missing}, **projections.week_projections(client, sid, season, week, missing, positions, nfl_teams)}
            cache.store(proj_key, PROJ_TTL, proj)
        except Exception:
            pass
    try:
        ranks = rank_job.result()
    except Exception:
        ranks = {}
    try:
        lines, scoring = lines_job.result(), scoring_job.result()
    except Exception:
        lines, scoring = {}, {}
    try:
        games = games_job.result()
    except Exception:
        games = {}
    for side in result["teams"]:
        total = 0.0
        for p in side["players"] + side.get("bench", []):
            pid = p["player_id"]
            p["pos"] = positions.get(pid) or p.get("slot")
            p["team"] = nfl_teams.get(pid) or None
            p["status"] = "final" if final else home.player_status(home.NFLVERSE_TEAMS.get(p["team"] or "", p["team"] or ""), starts, now)
            nfl_game = games.get(p["team"] or "")
            if nfl_game and not final and p["status"] != "bye":  # ESPN's real game state beats the kickoff-time guess
                p["status"] = {"pre": "waiting", "in": "playing", "post": "final"}.get(nfl_game["state"], p["status"])
            p["game"] = nfl_live.game_line(nfl_game)
            kick = starts.get(home.NFLVERSE_TEAMS.get(p["team"] or "", p["team"] or ""))
            p["kickoff"] = kick.isoformat() if kick else None
            p["line"] = nfl_live.stat_line((lines.get(pid) or {}).get("stats"), p["pos"]) if p["status"] in ("playing", "final") else []
            p["proj"] = proj.get(pid)
            p["rank"] = (ranks.get(pid) or {}).get("label") if p["status"] in ("playing", "final") and p["points"] else None
            p["breakdown"] = projections.breakdown((lines.get(pid) or {}).get("stats"), scoring) if p["status"] in ("playing", "final") else []
            p["live_proj"] = nfl_live.live_projection(p["points"], p["proj"], p["status"], p.get("game"))
        for p in side["players"]:
            total += p["live_proj"] if p["live_proj"] is not None else p["points"]
        side["projected"] = round(total, 1)
        if "bench" in side:
            side["bench"].sort(key=lambda p: (BENCH_ORDER.get(p["pos"] or "", len(BENCH_ORDER)), -(p["live_proj"] if p["live_proj"] is not None else p["points"])))
