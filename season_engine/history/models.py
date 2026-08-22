from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceBundle:
    league: dict[str, Any]
    users: tuple[dict[str, Any], ...]
    rosters: tuple[dict[str, Any], ...]
    matchups_by_week: dict[int, tuple[dict[str, Any], ...]]
    winners_bracket: tuple[dict[str, Any], ...]
    losers_bracket: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CapturePlan:
    league_id: str
    league_season_id: str
    season: int
    sleeper_league_id: str
    generated_at: str
    source_fingerprint: str
    idempotency_key: str
    canonical_team_count: int = 0
    canonical_team_set_fingerprint: str = ""
    source_roster_set_fingerprint: str = ""
    mapping_set_fingerprint: str = ""
    standings_set_fingerprint: str = ""
    source_roster_identifiers: tuple[dict[str, Any], ...] = ()
    team_mappings: tuple[dict[str, Any], ...] = ()
    matchups: tuple[dict[str, Any], ...] = ()
    standings: tuple[dict[str, Any], ...] = ()
    brackets: tuple[dict[str, Any], ...] = ()
    roster_assignments: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    blocking_errors: tuple[dict[str, Any], ...] = ()
    existing_counts: dict[str, int] = field(default_factory=dict)

    @property
    def safe_to_apply(self) -> bool:
        return not self.blocking_errors

    @property
    def expected_counts(self) -> dict[str, int]:
        return {
            "team_mappings": len(self.team_mappings),
            "matchups": len(self.matchups),
            "standings": len(self.standings),
            "brackets": len(self.brackets),
            "roster_assignments": len(self.roster_assignments),
        }

    def as_payload(self) -> dict[str, Any]:
        return {
            "league_id": self.league_id, "league_season_id": self.league_season_id,
            "season": self.season, "sleeper_league_id": self.sleeper_league_id,
            "source_fingerprint": self.source_fingerprint, "idempotency_key": self.idempotency_key,
            "canonical_team_count": self.canonical_team_count,
            "canonical_team_set_fingerprint": self.canonical_team_set_fingerprint,
            "source_roster_set_fingerprint": self.source_roster_set_fingerprint,
            "mapping_set_fingerprint": self.mapping_set_fingerprint,
            "standings_set_fingerprint": self.standings_set_fingerprint,
            "source_roster_identifiers": list(self.source_roster_identifiers),
            "team_mappings": list(self.team_mappings), "matchups": list(self.matchups),
            "standings": list(self.standings), "brackets": list(self.brackets),
            "roster_assignments": list(self.roster_assignments), "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class CaptureResult:
    dry_run: bool
    applied: bool
    plan: CapturePlan
    database_result: dict[str, Any] | None = None
