"""The league's game week, decided by us: which NFL week is being played and which weeks are final.

A week is final the moment its last game is over. We know a game is over from our own feeds, in this order:
  1. the live watcher (scripts/live_watcher.py) saved the game with ESPN's state "post",
  2. our nflverse schedule copy (nfl_games) has the final score, or
  3. the daily official-lines job (scripts/final_scores.py) saved the week, which it only does once every game is over.
A game whose day is more than MAX_OPEN_DAYS behind us counts as over too, so a cancelled game can't hold the season.

current = the first regular-season week that isn't final (after Monday night's last game ends, the next week starts).
last_final = the newest final week. Standings, results and the bracket count every week up to last_final; points
always come from our own engine (api.own_league), and stat corrections re-save the official lines, so a correction
flows into results and standings on its own. Sleeper's week is only the fallback when our schedule isn't loaded.

Shared by the iOS app's server and the website (services/app_context.py), so both always agree on the week.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from api import cache

REGULAR_WEEKS = 18
MAX_OPEN_DAYS = 3
TTL = 20  # seconds; the live watcher saves every few seconds, so the week flips within ~20 s of the last whistle

_client: Any = None


def remember(client: Any) -> None:
    """Keep a signed-in client to read the schedule with (league pages call this on every request)."""
    global _client
    if client is not None:
        _client = client


def season_of(today: date) -> int:
    """The NFL season a date belongs to (January and February are the previous season)."""
    return today.year if today.month >= 3 else today.year - 1


def _code(team: Any) -> str:
    from league_ai.nfl_data import team_code
    return team_code(team)


def decide(schedule: Iterable[Mapping[str, Any]], over_teams: Mapping[int, set[str]], saved_weeks: set[int],
           today: date) -> dict[str, Any] | None:
    """{"current", "last_final", "final"} from the season's regular-season games, or None without a schedule.

    over_teams: week -> teams whose game the live watcher saw end. saved_weeks: weeks with official lines saved."""
    weeks: dict[int, list[bool]] = {}
    for g in schedule:
        try:
            week = int(g.get("week") or 0)
        except (TypeError, ValueError):
            continue
        if not 1 <= week <= REGULAR_WEEKS:
            continue
        home, away = _code(g.get("home_team")), _code(g.get("away_team"))
        seen = over_teams.get(week) or set()
        over = (home in seen and away in seen) or (g.get("home_score") is not None and g.get("away_score") is not None)
        try:
            day = date.fromisoformat(str(g.get("gameday") or "")[:10])
            over = over or today - day > timedelta(days=MAX_OPEN_DAYS)
        except ValueError:
            pass
        weeks.setdefault(week, []).append(over or week in saved_weeks)
    if not weeks:
        return None
    final = [w for w in sorted(weeks) if all(weeks[w])]
    open_weeks = [w for w in sorted(weeks) if not all(weeks[w])]
    current = open_weeks[0] if open_weeks else max(weeks)
    last_final = max((w for w in final if w < current or not open_weeks), default=0)
    return {"current": current, "last_final": last_final, "final": final}


def load(client: Any, season: int, today: date | None = None) -> dict[str, Any] | None:
    """Read the schedule and our finals signals, then decide(). None when nothing can be read."""
    today = today or datetime.now(timezone.utc).date()
    games = (client.table("nfl_games").select("week,gameday,home_team,away_team,home_score,away_score")
             .eq("season", season).eq("game_type", "REG").limit(400).execute().data or [])
    if not games:
        return None
    open_weeks = sorted({int(g["week"]) for g in games if g.get("week") is not None
                         and (g.get("home_score") is None or g.get("away_score") is None)})
    over: dict[int, set[str]] = {}
    if open_weeks:
        try:  # only the weeks nflverse hasn't closed yet; one small read (game states, not the stat lines)
            rows = (client.table("live_stat_lines").select("week,games").eq("season", season)
                    .in_("week", open_weeks[:2]).execute().data or [])
        except Exception:
            rows = []
        for r in rows:
            for team, g in (r.get("games") or {}).items():
                if str((g or {}).get("state") or "") == "post":
                    over.setdefault(int(r["week"]), set()).add(_code(team))
    try:
        saved = {int(r["week"]) for r in (client.table("final_stat_lines").select("week").eq("season", season)
                                          .execute().data or [])}
    except Exception:
        saved = set()
    return decide(games, over, saved, today)


def state(client: Any = None, season: int | None = None) -> dict[str, Any] | None:
    """The shared game-week state (cached TTL seconds), or None when our schedule can't be read."""
    client = client or _client
    if client is None:
        return None
    season = season or season_of(datetime.now(timezone.utc).date())
    try:
        return cache.cached(("game_week", season), TTL, lambda: load(client, season))
    except Exception as exc:
        print(f"GAME_WEEK_ERROR {type(exc).__name__}", flush=True)
        return None
