from __future__ import annotations

from auth import service_client
from season_engine import SeasonResolver
from season_engine.service import resolve_single_league_id


class SeasonContext:
    """
    Central source of truth for season-related settings.
    """

    def __init__(self):
        self.sb = service_client()

    def current_season(self, league_id: str | None = None) -> int:
        resolved_league_id = league_id or resolve_single_league_id(self.sb)
        return SeasonResolver(self.sb).get_active_season(resolved_league_id).season

    def rookie_class_year(self, league_id: str | None = None) -> int:
        return self.current_season(league_id)


season_context = SeasonContext()
