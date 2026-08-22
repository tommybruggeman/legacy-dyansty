from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class SeasonStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    SCHEDULED = "scheduled"


@dataclass(frozen=True)
class LeagueSeason:
    id: str | None
    league_id: str
    season: int
    sleeper_league_id: str | None
    is_active: bool
    created_at: str | None = None

    def status_relative_to(self, active_season: int) -> SeasonStatus:
        if self.is_active:
            return SeasonStatus.ACTIVE
        return SeasonStatus.COMPLETED if self.season < active_season else SeasonStatus.SCHEDULED

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "LeagueSeason":
        league_id = str(row.get("league_id") or "").strip()
        try:
            season = int(row.get("season"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid league season value: {row.get('season')!r}") from exc
        if not league_id:
            raise ValueError("League season row is missing league_id.")
        if not 2000 <= season <= 2100:
            raise ValueError(f"League season is outside the supported range: {season}")
        return cls(
            id=_text(row.get("id")),
            league_id=league_id,
            season=season,
            sleeper_league_id=_text(row.get("sleeper_league_id")),
            is_active=row.get("is_active") is True,
            created_at=_text(row.get("created_at")),
        )


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
