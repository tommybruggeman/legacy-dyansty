from __future__ import annotations

from typing import Any

from season_engine.models import LeagueSeason
from season_engine.resolver import SeasonAuthorityError, SeasonResolver


def get_active_season(league_id: str, *, client: Any | None = None) -> LeagueSeason:
    return SeasonResolver(client).get_active_season(league_id)


def get_completed_season(league_id: str, *, client: Any | None = None) -> LeagueSeason:
    return SeasonResolver(client).get_completed_season(league_id)


def get_next_season(league_id: str, *, client: Any | None = None) -> int:
    return SeasonResolver(client).get_next_season(league_id)


def resolve_single_league_id(client: Any) -> str:
    rows = client.table("leagues").select("id").execute().data or []
    ids = sorted({str(row.get("id") or "").strip() for row in rows if row.get("id")})
    if len(ids) != 1:
        raise SeasonAuthorityError(
            "A league_id is required when the database does not contain exactly one league."
        )
    return ids[0]
