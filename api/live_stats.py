"""Live NFL stat lines saved by our game-day watcher (scripts/live_watcher.py), read by the server.

The watcher reads ESPN's free feed every few seconds and saves one row per NFL game in live_stat_lines:
its players' stat lines in our stat keys, plus the game's state. The server reads those rows (shared by
every owner, at most once per TTL seconds) and scores them with our engine, so live points come from our own
pipeline instead of Sleeper's. Sleeper stays the fallback until a week has our lines.

Finished weeks read final_stat_lines instead: the official lines (nflverse, our own copy) saved and locked by the
daily final-scores job.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from api import cache
from scoring import engine

TABLE = "live_stat_lines"
TTL = 10  # one read per server instance per 10 s, shared by every owner
STALE = 20 * 60  # lines older than this (watcher down) stop overriding live points


def saved(client: Any, season: int, week: int) -> dict[str, Any] | None:
    """{lines, games, source, updated_at} for one week (every game's row merged; updated_at is the newest save or
    heartbeat), or None when the watcher hasn't saved it."""
    def load() -> dict[str, Any] | None:
        rows = (client.table(TABLE).select("game_id,lines,games,source,updated_at").eq("season", season).eq("week", week)
                .execute().data or [])
        if not rows:
            return None
        lines: dict[str, Any] = {}
        games: dict[str, Any] = {}
        for row in rows:
            lines.update(row.get("lines") or {})
            games.update(row.get("games") or {})
        newest = max(rows, key=lambda r: str(r.get("updated_at") or ""))
        return {"lines": lines, "games": games, "source": newest.get("source"), "updated_at": newest.get("updated_at")}
    try:
        return cache.cached((TABLE, season, week), TTL, load)
    except Exception:
        return None


def age(row: Mapping[str, Any] | None) -> float | None:
    try:
        stamp = datetime.fromisoformat(str((row or {}).get("updated_at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def lines(client: Any, season: int, week: int) -> dict[str, dict[str, Any]]:
    """player id -> {stats, pos} from the watcher, empty when there's no row."""
    return dict((saved(client, season, week) or {}).get("lines") or {})


def fresh_lines(client: Any, season: int, week: int) -> dict[str, dict[str, Any]]:
    """Like lines(), but empty when the watcher stopped updating (so a dead watcher never freezes live scores)."""
    row = saved(client, season, week)
    a = age(row)
    return dict(row.get("lines") or {}) if row and a is not None and a <= STALE else {}


def rescore(rows: list[Mapping[str, Any]], lines: Mapping[str, Mapping[str, Any]], scoring: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Sleeper-shaped matchup rows with each player we have a line for scored by our engine (others keep the
    row's own number), and each team's total redone from its starters."""
    out = []
    for row in rows:
        starters = [str(p) for p in row.get("starters") or []]
        old_starters = list(row.get("starters_points") or [])
        old_players = dict(row.get("players_points") or {})

        def pts(pid: str, old: Any) -> float:
            line = lines.get(pid) if pid not in ("", "0") else None
            return engine.player_points(line.get("stats"), scoring, line.get("pos")) if line else float(old or 0)

        starters_points = [pts(pid, old_starters[i] if i < len(old_starters) else 0) for i, pid in enumerate(starters)]
        players_points = {str(pid): pts(str(pid), old_players.get(str(pid))) for pid in row.get("players") or []}
        out.append({**row, "starters_points": starters_points, "players_points": players_points,
                    "points": round(sum(starters_points), 2), "custom_points": None})
    return out


FINAL_TABLE = "final_stat_lines"
FINAL_TTL = 3600


def final_lines(client: Any, season: int, week: int) -> dict[str, dict[str, Any]]:
    """player id -> {stats, pos}: the week's official lines from our own copy (scripts/final_scores.py), empty until
    the daily job has saved the week."""
    def load() -> dict[str, Any]:
        rows = (client.table(FINAL_TABLE).select("lines").eq("season", season).eq("week", week).limit(1).execute().data or [])
        return dict(rows[0].get("lines") or {}) if rows else {}
    try:
        return cache.cached((FINAL_TABLE, season, week), FINAL_TTL, load)
    except Exception:
        return {}
