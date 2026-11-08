"""The website's matchup rows, with every point scored by our own engine (the same copy the iOS app reads).

api.own_league.sync rescores each week's Sleeper rows from our own stat lines (live watcher, official finals, and
Sleeper's public stats only as a backup) and keeps them in api.sleeper's shared overrides; this reads through them.
Pages call matchup_rows instead of fetching Sleeper's matchups URL themselves.
"""
from __future__ import annotations

from typing import Any

from api import cache, own_league, sleeper

LIVE_TTL = 15


def _league_for(client: Any, sleeper_league_id: str) -> tuple[str, int] | None:
    def load() -> dict[str, Any]:
        rows = (client.table("league_seasons").select("league_id,season")
                .eq("sleeper_league_id", sleeper_league_id).limit(1).execute().data or [])
        return rows[0] if rows else {}
    row = cache.cached(("own_scores_league", sleeper_league_id), 600, load)
    return (str(row["league_id"]), int(row["season"])) if row else None


def sync(client: Any, sleeper_league_id: str) -> None:
    """Refresh our scores for this Sleeper league (shared, at most once per 15 s). Never raises."""
    if not client or not sleeper_league_id:
        return
    try:
        found = _league_for(client, sleeper_league_id)
        if found:
            own_league.sync(client, found[0], sleeper_league_id, found[1])
    except Exception:
        pass


def matchup_rows(client: Any, sleeper_league_id: str, week: int) -> list[dict[str, Any]]:
    """Sleeper-shaped matchup rows for one week, scored by us when we can (Sleeper's own rows otherwise)."""
    sync(client, sleeper_league_id)
    return sleeper.get(f"league/{sleeper_league_id}/matchups/{week}", ttl=LIVE_TTL) or []
