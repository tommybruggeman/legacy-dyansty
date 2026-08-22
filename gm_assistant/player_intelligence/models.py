from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlayerIntelligenceAvailability(str, Enum):
    FOUND_COMPLETE_ENOUGH = "found_complete_enough"
    PARTIAL = "partial"
    IDENTITY_ONLY = "identity_only"
    GLOBAL_INTELLIGENCE_UNAVAILABLE = "global_intelligence_unavailable"
    SCOPED_CONTEXT_UNAVAILABLE = "scoped_context_unavailable"
    AMBIGUOUS_IDENTITY = "ambiguous_identity"
    NOT_FOUND = "not_found"
    MALFORMED_SOURCE_DATA = "malformed_source_data"


@dataclass(frozen=True)
class PlayerIntelligenceLineage:
    domain: str
    source_name: str
    scope: str
    league_id: str | None = None
    league_team_id: str | None = None
    player_id: str | None = None
    status: str = "success"


@dataclass(frozen=True)
class PlayerIdentity:
    player_id: str | None
    sleeper_id: str | None
    player_name: str | None
    position: str | None = None
    nfl_team: str | None = None


@dataclass(frozen=True)
class PlayerLeagueContext:
    league_id: str | None = None
    league_team_id: str | None = None
    fantasy_team_name: str | None = None
    owner_name: str | None = None
    roster_status: str | None = None
    roster_designation: str | None = None
    is_free_agent: bool | None = None
    is_taxi: bool | None = None
    is_ir: bool | None = None
    salary: float | None = None
    contract_years_remaining: int | None = None
    contract_status: str | None = None


@dataclass(frozen=True)
class PlayerFieldConflict:
    field: str
    selected_value: Any
    rejected_value: Any
    selected_source: str
    conflicting_source: str


@dataclass(frozen=True)
class PlayerIntelligenceCompleteness:
    present_groups: tuple[str, ...] = ()
    missing_groups: tuple[str, ...] = ()
    available_sources: tuple[str, ...] = ()
    unavailable_sources: tuple[str, ...] = ()
    league_context_requested: bool = False
    league_context_resolved: bool = False


@dataclass(frozen=True)
class PlayerIntelligenceProfile:
    identity: PlayerIdentity
    availability: str
    completeness: PlayerIntelligenceCompleteness
    career_context: dict[str, Any] = field(default_factory=dict)
    global_intelligence: dict[str, Any] = field(default_factory=dict)
    strategic_profile: dict[str, Any] = field(default_factory=dict)
    league_relative_value: dict[str, Any] = field(default_factory=dict)
    league_context: PlayerLeagueContext = field(default_factory=PlayerLeagueContext)
    derived_fields: dict[str, Any] = field(default_factory=dict)
    conflicts: list[PlayerFieldConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lineage: list[PlayerIntelligenceLineage] = field(default_factory=list)

    def to_evidence_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "player_id": self.identity.player_id,
            "sleeper_id": self.identity.sleeper_id,
            "player_name": self.identity.player_name,
            "position": self.identity.position,
            "nfl_team": self.identity.nfl_team,
            "availability": self.availability,
            "profile_completeness": {
                "present_groups": list(self.completeness.present_groups),
                "missing_groups": list(self.completeness.missing_groups),
                "available_sources": list(self.completeness.available_sources),
                "unavailable_sources": list(self.completeness.unavailable_sources),
                "league_context_requested": self.completeness.league_context_requested,
                "league_context_resolved": self.completeness.league_context_resolved,
            },
            **self.career_context,
            **self.global_intelligence,
            **self.strategic_profile,
            **self.league_relative_value,
            **self.derived_fields,
        }
        if self.league_context.league_id:
            row["league_id"] = self.league_context.league_id
        if self.league_context.league_team_id:
            row["league_team_id"] = self.league_context.league_team_id
            row["team_id"] = self.league_context.league_team_id
        if self.league_context.fantasy_team_name:
            row["team_name"] = self.league_context.fantasy_team_name
            row["owner_team_name"] = self.league_context.fantasy_team_name
        if self.league_context.owner_name:
            row["owner_name"] = self.league_context.owner_name
        if self.league_context.roster_status:
            row["status"] = self.league_context.roster_status
        if self.league_context.salary is not None:
            row["salary"] = self.league_context.salary
        if self.league_context.contract_years_remaining is not None:
            row["contract_years_left"] = self.league_context.contract_years_remaining
        if self.league_context.contract_status:
            row["contract_status"] = self.league_context.contract_status
        return {key: value for key, value in row.items() if value is not None}
