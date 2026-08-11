from __future__ import annotations

from typing import Any

from season_engine.resolver import SeasonNotFoundError


TABLES = {
    "team_mappings": "season_team_mappings", "standings": "season_standings",
    "matchups": "season_matchups", "brackets": "season_playoff_brackets",
    "roster_assignments": "season_roster_assignments",
}


class HistoricalSeasonRepository:
    """Explicit season-scoped historical reads; no active-season fallback exists."""

    def __init__(self, client: Any): self.client = client

    def league_season_id(self, league_id: str, season: int) -> str:
        if not league_id or season is None:
            raise ValueError("Historical reads require explicit league_id and season.")
        rows = (self.client.table("league_seasons").select("id").eq("league_id", league_id)
                .eq("season", int(season)).execute().data or [])
        if len(rows) != 1:
            raise SeasonNotFoundError(f"Expected one league_seasons row for {league_id!r}/{season}; found {len(rows)}.")
        return str(rows[0]["id"])

    def get_season_team_mappings(self, league_id: str, season: int): return self._read("team_mappings", league_id, season)
    def get_season_standings(self, league_id: str, season: int): return self._read("standings", league_id, season)
    def get_season_matchups(self, league_id: str, season: int, week: int | None = None):
        return self._read("matchups", league_id, season, week=week)
    def get_season_playoff_result(self, league_id: str, season: int): return self._read("brackets", league_id, season)
    def get_season_final_roster(self, league_id: str, season: int, league_team_id: str):
        if not league_team_id: raise ValueError("league_team_id is required.")
        return self._read("roster_assignments", league_id, season, league_team_id=league_team_id)

    def counts(self, league_season_id: str) -> dict[str, int]:
        result = {}
        for key, table in TABLES.items():
            rows = self.client.table(table).select("id").eq("league_season_id", league_season_id).execute().data or []
            result[key] = len(rows)
        return result

    def _read(self, kind, league_id, season, **filters):
        query = self.client.table(TABLES[kind]).select("*").eq("league_season_id", self.league_season_id(league_id, season))
        for key, value in filters.items():
            if value is not None: query = query.eq(key, value)
        return query.execute().data or []
