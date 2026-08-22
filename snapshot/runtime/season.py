from __future__ import annotations

from auth import service_client
from season_engine import SeasonResolver
from season_engine.service import resolve_single_league_id


def get_current_season(league_id: str | None = None) -> int:
    sb = service_client()
    resolved_league_id = league_id or resolve_single_league_id(sb)
    return SeasonResolver(sb).get_active_season(resolved_league_id).season
