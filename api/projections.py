"""Our own weekly projections and weekly position ranks.

Sleeper publishes a projected stat line and a real stat line for every NFL
player each week. Both are scored here with this league's own scoring rules
(league/{id} scoring_settings), so the numbers match how Legacy scores.
Projections are then tilted by the same matchup context the Front Office
uses (defense vs position, Vegas total, weather), capped at +/-20%.
"""
from __future__ import annotations

from typing import Any, Iterable

from api import cache, sleeper
from league_ai.nfl_data import add_weather, defense_vs_position, season_games, team_code, total_multiplier, week_matchups

ROOT = "https://api.sleeper.app"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
_QUERY = "season_type=regular&" + "&".join(f"position[]={p}" for p in POSITIONS)


def score(stats: dict[str, Any] | None, scoring: dict[str, Any]) -> float:
    """Points for one stat line under the league's scoring rules (Sleeper stat keys match scoring keys)."""
    if not stats:
        return 0.0
    total = 0.0
    for key, weight in scoring.items():
        value = stats.get(key)
        if value is None:
            continue
        try:
            total += float(value) * float(weight)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def _rows(payload: Any) -> dict[str, dict[str, Any]]:
    """player_id -> {stats, pos, team}, from either Sleeper response shape (list of rows or id -> stats)."""
    out: dict[str, dict[str, Any]] = {}
    if isinstance(payload, list):
        for row in payload:
            pid = str(row.get("player_id") or "")
            if not pid:
                continue
            player = row.get("player") or {}
            out[pid] = {"stats": row.get("stats") or {}, "pos": str(player.get("position") or row.get("position") or "").upper(),
                        "team": team_code(row.get("team") or player.get("team"))}
    elif isinstance(payload, dict):
        for pid, stats in payload.items():
            out[str(pid)] = {"stats": stats or {}, "pos": "", "team": ""}
    return out


def week_lines(kind: str, season: int, week: int, ttl: int) -> dict[str, dict[str, Any]]:
    """kind is 'stats' or 'projections'. Empty on any failure."""
    try:
        return _rows(sleeper.get_url(f"{ROOT}/{kind}/nfl/{season}/{week}?{_QUERY}", ttl=ttl))
    except Exception:
        return {}


def league_scoring(sleeper_id: str) -> dict[str, Any]:
    try:
        return (sleeper.get(f"league/{sleeper_id}", ttl=3600) or {}).get("scoring_settings") or {}
    except Exception:
        return {}


def ranks(lines: dict[str, dict[str, Any]], scoring: dict[str, Any], positions: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """player_id -> {rank, pos, points}: position rank among everyone who scored that week."""
    by_pos: dict[str, list[tuple[str, float]]] = {}
    for pid, line in lines.items():
        pos = line.get("pos") or (positions or {}).get(pid, "")
        if not pos or not line.get("stats"):
            continue
        pts = score(line["stats"], scoring)
        if pts == 0 and not line["stats"].get("gp") and not line["stats"].get("off_snp"):
            continue
        by_pos.setdefault(pos, []).append((pid, pts))
    out: dict[str, dict[str, Any]] = {}
    for pos, rows in by_pos.items():
        rows.sort(key=lambda r: r[1], reverse=True)
        for rank, (pid, pts) in enumerate(rows, start=1):
            out[pid] = {"rank": rank, "pos": pos, "label": f"{pos}{rank}", "points": pts}
    return out


def week_ranks(sleeper_id: str, season: int, week: int, final: bool, positions: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    def load() -> dict[str, dict[str, Any]]:
        return ranks(week_lines("stats", season, week, 86400 if final else 120), league_scoring(sleeper_id), positions)
    # A finished week's ranks never change, so every player page shares one scoring pass.
    return cache.cached(("week_ranks", sleeper_id, season, week), 21600, load) if final else load()


def week_projections(client: Any, sleeper_id: str, season: int, week: int, ids: Iterable[str], positions: dict[str, str], teams: dict[str, str]) -> dict[str, float]:
    """player_id -> projected league points this week, matchup-adjusted."""
    lines = week_lines("projections", season, week, 1800)
    scoring = league_scoring(sleeper_id)
    try:
        games = add_weather(week_matchups(season_games(client, season), week), season, week)
    except Exception:
        games = {}
    try:
        dvp = defense_vs_position(client, season)
    except Exception:
        dvp = {}
    out: dict[str, float] = {}
    for pid in ids:
        line = lines.get(pid)
        if not line:
            continue
        base = score(line["stats"], scoring)
        pos = positions.get(pid) or line.get("pos") or ""
        team = team_code(teams.get(pid) or line.get("team"))
        game = games.get(team)
        if game is None and games:
            out[pid] = 0.0  # on bye
            continue
        factor = total_multiplier(pos, game, dvp.get((game["opponent"], pos)) if game else None) if pos in ("QB", "RB", "WR", "TE") else 1.0
        out[pid] = round(base * factor, 1)
    return out
