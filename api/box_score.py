"""One game's box score with each starter's projection, game status, weekly position rank and stat breakdown.

Shared by the app server (/matchups/{week}/{matchup_id}) and the website's Weekly Matchups page, so both show the same numbers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from api import cache, home, league, projections, teams
from api.front_office import LEAGUE_TZ


def game(client: Any, ctx: dict[str, Any], week: int, matchup_id: int, team_rows: Iterable[dict[str, Any]] = ()) -> dict[str, Any] | None:
    """ctx needs season, sleeper_id, names (roster id -> owner), slots and sleeper_roster_id. None when there's no such game."""
    final = week <= league.last_scored_week(ctx["sleeper_id"])
    ttl = 86400 if final else league.LIVE_TTL
    ids = teams.starter_ids(ctx["sleeper_id"], week, matchup_id, ttl)
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
    return result


def week_kickoffs(client: Any, season: int, week: int) -> dict[str, datetime]:
    try:
        nfl = cache.cached(("nfl_games", season, week), 600, lambda: client.table("nfl_games").select("gameday,gametime,home_team,away_team")
                           .eq("season", season).eq("week", week).execute().data or [])
    except Exception:
        nfl = []
    return home.kickoffs(nfl)


def add_projections(client: Any, ctx: dict[str, Any], week: int, final: bool, result: dict[str, Any], info: dict[str, dict[str, Any]]) -> None:
    """Each starter's projection, game status and weekly position rank, plus each side's projected total.

    A side's projection counts real points for finished games, the higher of
    real and projected for games in progress, and the projection otherwise.
    """
    ids = [p["player_id"] for side in result["teams"] for p in side["players"] if p["player_id"] not in ("", "0")]
    positions = {pid: str(info.get(pid, {}).get("position") or "") for pid in ids}
    nfl_teams = {pid: str(info.get(pid, {}).get("team") or "") for pid in ids}
    season, sid = ctx["season"], ctx["sleeper_id"]
    proj_job = cache.pool.submit(lambda: cache.cached(("proj", sid, season, week), 900, lambda: projections.week_projections(client, sid, season, week, ids, positions, nfl_teams)))
    rank_job = cache.pool.submit(lambda: projections.week_ranks(sid, season, week, final, positions))
    # week_ranks reads the same stat lines, so this second read comes from the Sleeper cache.
    lines_job = cache.pool.submit(lambda: projections.week_lines("stats", season, week, 86400 if final else 120))
    scoring_job = cache.pool.submit(lambda: projections.league_scoring(sid))
    starts = week_kickoffs(client, season, week)
    now = datetime.now(LEAGUE_TZ)
    try:
        proj = proj_job.result()
    except Exception:
        proj = {}
    if set(ids) - set(proj):  # cached projections were for another lineup: score these too
        try:
            proj = {**proj, **projections.week_projections(client, sid, season, week, set(ids) - set(proj), positions, nfl_teams)}
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
    for side in result["teams"]:
        total = 0.0
        for p in side["players"]:
            pid = p["player_id"]
            p["pos"] = positions.get(pid) or p.get("slot")
            p["team"] = nfl_teams.get(pid) or None
            p["status"] = "final" if final else home.player_status(home.NFLVERSE_TEAMS.get(p["team"] or "", p["team"] or ""), starts, now)
            p["proj"] = proj.get(pid)
            p["rank"] = (ranks.get(pid) or {}).get("label") if p["status"] in ("playing", "final") and p["points"] else None
            p["breakdown"] = projections.breakdown((lines.get(pid) or {}).get("stats"), scoring) if p["status"] in ("playing", "final") else []
            if p["status"] == "final" or p["proj"] is None:
                total += p["points"]
            elif p["status"] == "playing":
                total += max(p["points"], p["proj"])
            else:
                total += p["proj"]
        side["projected"] = round(total, 1)
