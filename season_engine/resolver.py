from __future__ import annotations

from typing import Any, Iterable, Mapping

from season_engine.models import LeagueSeason


class SeasonAuthorityError(RuntimeError):
    """Base error for invalid or unavailable league-season authority."""


class SeasonNotFoundError(SeasonAuthorityError):
    pass


class DuplicateActiveSeasonError(SeasonAuthorityError):
    pass


class DuplicateLeagueSeasonError(SeasonAuthorityError):
    pass


class SeasonResolver:
    """Resolve league time exclusively from public.league_seasons."""

    def __init__(self, client: Any | None = None):
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from auth import service_client

            self._client = service_client()
        return self._client

    def get_active_season(self, league_id: str) -> LeagueSeason:
        seasons = self._load_and_validate(league_id)
        active = [row for row in seasons if row.is_active]
        if not active:
            raise SeasonNotFoundError(
                f"League {league_id!r} has no active league_seasons row."
            )
        if len(active) != 1:
            years = sorted(row.season for row in active)
            raise DuplicateActiveSeasonError(
                f"League {league_id!r} must have exactly one active season; found {years}."
            )
        return active[0]

    def get_completed_season(self, league_id: str) -> LeagueSeason:
        seasons = self._load_and_validate(league_id)
        active = self.get_active_season(league_id)
        completed = [row for row in seasons if not row.is_active and row.season < active.season]
        if not completed:
            raise SeasonNotFoundError(
                f"League {league_id!r} has no completed season before {active.season}."
            )
        return max(completed, key=lambda row: row.season)

    def get_next_season(self, league_id: str) -> int:
        return self.get_active_season(league_id).season + 1

    def _load_and_validate(self, league_id: str) -> tuple[LeagueSeason, ...]:
        clean_id = str(league_id or "").strip()
        if not clean_id:
            raise SeasonNotFoundError("A league_id is required to resolve the active season.")
        try:
            rows = (
                self.client.table("league_seasons")
                .select("id,league_id,season,sleeper_league_id,is_active,created_at")
                .eq("league_id", clean_id)
                .execute()
                .data
                or []
            )
        except SeasonAuthorityError:
            raise
        except Exception as exc:
            raise SeasonAuthorityError(
                f"Could not read authoritative league seasons for {clean_id!r}: {exc}"
            ) from exc
        seasons = _parse_rows(rows, clean_id)
        if not seasons:
            raise SeasonNotFoundError(
                f"League {clean_id!r} has no authoritative league_seasons rows."
            )
        return seasons


def _parse_rows(rows: Iterable[Mapping[str, Any]], league_id: str) -> tuple[LeagueSeason, ...]:
    parsed: list[LeagueSeason] = []
    seen: set[int] = set()
    for raw in rows:
        try:
            row = LeagueSeason.from_row(raw)
        except ValueError as exc:
            raise SeasonAuthorityError(
                f"Invalid league_seasons row for league {league_id!r}: {exc}"
            ) from exc
        if row.league_id != league_id:
            raise SeasonAuthorityError(
                f"Season query for {league_id!r} returned a row for {row.league_id!r}."
            )
        if row.season in seen:
            raise DuplicateLeagueSeasonError(
                f"League {league_id!r} has duplicate league_seasons rows for {row.season}."
            )
        seen.add(row.season)
        parsed.append(row)
    active = [row.season for row in parsed if row.is_active]
    if len(active) > 1:
        raise DuplicateActiveSeasonError(
            f"League {league_id!r} must have exactly one active season; found {sorted(active)}."
        )
    return tuple(parsed)
