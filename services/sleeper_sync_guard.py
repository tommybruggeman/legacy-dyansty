from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.strict_pagination import complete_rows


class SleeperSyncGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class SleeperSyncAuthority:
    league_id: str
    league_season_id: str
    season: int
    sleeper_league_id: str


def require_active_season_sync_authority(client: Any, *, league_id: str, expected_season: int,
                                         sleeper_league_id: str) -> SleeperSyncAuthority:
    """Fail closed before any destructive legacy roster mirror write."""
    if not league_id or not sleeper_league_id or not expected_season:
        raise SleeperSyncGuardError("Explicit league, season, and Sleeper league identity are required.")
    seasons = complete_rows(client, "league_seasons", filters={"league_id": league_id}, order_key="id")
    active = [row for row in seasons if row.get("is_active") and row.get("status") == "active"]
    if len(active) != 1:
        raise SleeperSyncGuardError("Exactly one canonical active season is required.")
    row = active[0]
    if int(row.get("season") or 0) != int(expected_season):
        raise SleeperSyncGuardError("Requested sync season does not match canonical active season.")
    if str(row.get("sleeper_league_id") or "") != str(sleeper_league_id):
        raise SleeperSyncGuardError("Sleeper league identity does not match canonical active-season authority.")
    locks = complete_rows(client, "rollover_execution_locks",
                          filters={"league_id": league_id, "status": "active"}, order_key="id")
    if any(lock.get("lock_type") == "cutover" for lock in locks):
        raise SleeperSyncGuardError("Roster synchronization is blocked by the active rollover cutover lock.")
    return SleeperSyncAuthority(str(league_id), str(row["id"]), int(row["season"]), str(sleeper_league_id))
