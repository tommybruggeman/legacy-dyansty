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


# Sleeper stat keys -> short labels for a score breakdown. Keys not listed still
# count; they show under a tidied version of the key.
LABELS = {
    "pass_yd": "pass yds", "pass_td": "pass TD", "pass_int": "INT", "pass_2pt": "pass 2pt", "pass_cmp": "completions",
    "pass_att": "pass att", "pass_inc": "incompletions", "pass_sack": "sacks taken",
    "rush_yd": "rush yds", "rush_td": "rush TD", "rush_att": "carries", "rush_2pt": "rush 2pt",
    "rec": "catches", "rec_yd": "rec yds", "rec_td": "rec TD", "rec_2pt": "rec 2pt", "rec_tgt": "targets",
    "fum_lost": "fumbles lost", "fum": "fumbles", "fum_rec_td": "fumble return TD",
    "xpm": "XP made", "xpmiss": "XP missed", "fgmiss": "FG missed",
    "fgm_0_19": "FG 0-19", "fgm_20_29": "FG 20-29", "fgm_30_39": "FG 30-39", "fgm_40_49": "FG 40-49", "fgm_50p": "FG 50+",
    "def_td": "D/ST TD", "sack": "sacks", "int": "INTs", "fum_rec": "fumble recoveries", "safe": "safeties", "blk_kick": "blocked kicks",
    "pts_allow_0": "0 pts allowed", "pts_allow_1_6": "1-6 pts allowed", "pts_allow_7_13": "7-13 pts allowed",
    "pts_allow_14_20": "14-20 pts allowed", "pts_allow_21_27": "21-27 pts allowed", "pts_allow_28_34": "28-34 pts allowed",
    "pts_allow_35p": "35+ pts allowed", "st_td": "return TD", "kr_td": "kick return TD", "pr_td": "punt return TD",
}


def breakdown(stats: dict[str, Any] | None, scoring: dict[str, Any]) -> list[dict[str, Any]]:
    """Where one stat line's points came from: [{label, value, points}], biggest first, zero rows dropped."""
    rows = []
    for key, weight in scoring.items():
        value = (stats or {}).get(key)
        try:
            pts = float(value) * float(weight) if value is not None else 0.0
        except (TypeError, ValueError):
            continue
        if abs(pts) < 0.005:
            continue
        value = float(value)
        rows.append({
            "label": LABELS.get(key) or key.replace("_", " "),
            "value": int(value) if value.is_integer() else round(value, 1),
            "points": round(pts, 2),
        })
    rows.sort(key=lambda r: -abs(r["points"]))
    return rows


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
