"""Read helpers over the nfl_* tables (freshness, lookups). Read-only."""
from __future__ import annotations

from typing import Any


def data_freshness(client: Any) -> dict[str, str]:
    """Latest successful run per loader, for the pack's freshness section."""
    out: dict[str, str] = {}
    if client is None:
        return out
    try:
        rows = (
            client.table("nfl_data_sync_log")
            .select("loader, finished_at, rows_written, ok")
            .eq("ok", True)
            .order("finished_at", desc=True)
            .limit(30)
            .execute()
            .data
            or []
        )
    except Exception:
        return {"NFL data": "not loaded yet (nightly sync has not run)"}
    labels = {"sleeper_players": "NFL players (Sleeper)", "nflverse_stats": "NFL stats (nflverse)", "dynastyprocess_values": "market values (DynastyProcess)", "injuries": "injuries (ESPN report + nflverse practice reports)", "cfbd_prospects": "rookie prospects (CFBD)", "league_history": "league history (Sleeper transactions, drafts, matchups)"}
    for row in rows:
        label = labels.get(row.get("loader"), row.get("loader"))
        if label not in out and row.get("finished_at"):
            out[label] = str(row["finished_at"])[:16].replace("T", " ") + " UTC"
    if not out:
        out["NFL data"] = "not loaded yet (nightly sync has not run)"
    return out


# nflverse schedule codes -> Sleeper team codes (nfl_players.team uses Sleeper's)
TEAM_ALIASES = {"LA": "LAR", "WSH": "WAS", "JAC": "JAX"}


def team_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    return TEAM_ALIASES.get(code, code)


def bye_weeks_from_games(rows: list[dict[str, Any]]) -> dict[str, int]:
    """NFL team -> bye week, from one season's regular-season nfl_games rows.

    A team's bye is the one week in the season's range it has no game. Teams with
    zero or several missing weeks (schedule not fully loaded) are left out rather than guessed.
    """
    played: dict[str, set[int]] = {}
    weeks: set[int] = set()
    for r in rows:
        try:
            week = int(r.get("week"))
        except Exception:
            continue
        weeks.add(week)
        for side in ("home_team", "away_team"):
            code = team_code(r.get(side))
            if code:
                played.setdefault(code, set()).add(week)
    if not weeks:
        return {}
    season_weeks = set(range(min(weeks), max(weeks) + 1))
    out: dict[str, int] = {}
    for code, seen in played.items():
        missing = season_weeks - seen
        if len(missing) == 1:
            out[code] = missing.pop()
    return out


def bye_weeks(client: Any, season: int) -> dict[str, int]:
    """One query over nfl_games; empty when the schedule isn't loaded."""
    if client is None:
        return {}
    try:
        rows = client.table("nfl_games").select("week, home_team, away_team").eq("season", season).eq("game_type", "REG").limit(400).execute().data or []
    except Exception:
        return {}
    return bye_weeks_from_games(rows)
