from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DraftIntelligenceAvailability(str, Enum):
    FULL_BOARD_CONTEXT_AVAILABLE = "full_board_context_available"
    PARTIAL_BOARD_CONTEXT = "partial_board_context"
    PICK_OWNERSHIP_AVAILABLE = "pick_ownership_available"
    EXACT_SLOT_UNAVAILABLE = "exact_slot_unavailable"
    PROSPECT_POOL_AVAILABLE = "prospect_pool_available"
    PROSPECT_POOL_UNAVAILABLE = "prospect_pool_unavailable"
    SELECTIONS_AVAILABLE = "selections_available"
    SELECTIONS_INCOMPLETE = "selections_incomplete"
    PROSPECT_PROFILE_PARTIAL = "prospect_profile_partial"
    DRAFT_CONTEXT_UNAVAILABLE = "draft_context_unavailable"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"
    MALFORMED_DATA = "malformed_data"


@dataclass(frozen=True)
class DraftLineage:
    domain: str
    source_name: str
    scope: str
    league_id: str | None = None
    league_team_id: str | None = None
    player_id: str | None = None
    status: str = "success"


@dataclass(frozen=True)
class ParsedPickReference:
    raw_text: str
    reference_type: str
    season: int | None = None
    round: int | None = None
    slot: int | None = None
    label: str | None = None
    current_owner_team_id: str | None = None
    resolution_status: str = "unresolved"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftSlot:
    season: int | None
    round: int | None
    overall_pick: int | None
    display_label: str | None
    selecting_team_id: str | None = None
    selection_status: str = "unknown"
    selected_player_id: str | None = None
    lineage: list[DraftLineage] = field(default_factory=list)


@dataclass(frozen=True)
class DraftPickAsset:
    league_id: str
    season: int | None
    round: int | None
    current_owner_team_id: str | None
    original_team_id: str | None
    pick_label: str | None
    original_pick_rank: int | None
    exact_slot: DraftSlot | None = None
    ownership_status: str = "unknown"
    availability_state: str = DraftIntelligenceAvailability.PICK_OWNERSHIP_AVAILABLE.value
    warnings: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    lineage: list[DraftLineage] = field(default_factory=list)

    @property
    def canonical_pick_id(self) -> str | None:
        if self.pick_label and self.season:
            return f"{self.season}_{self.pick_label}"
        if self.pick_label:
            return self.pick_label
        if self.season and self.round and self.original_team_id:
            return f"{self.season}_round_{self.round}_{self.original_team_id}"
        if self.season and self.round:
            return f"{self.season}_round_{self.round}"
        return None

    def to_evidence_row(self) -> dict[str, Any]:
        row = {
            "league_id": self.league_id,
            "canonical_pick_id": self.canonical_pick_id,
            "season": self.season,
            "round": self.round,
            "slot": self.exact_slot.overall_pick if self.exact_slot else None,
            "pick_label": self.pick_label,
            "original_pick_rank": self.original_pick_rank,
            "resolved_current_owner_team_id": self.current_owner_team_id,
            "resolved_original_team_id": self.original_team_id,
            "current_owner_team_id": self.current_owner_team_id,
            "original_team_id": self.original_team_id,
            "status": self.ownership_status,
            "availability_state": self.availability_state,
            "warnings": list(self.warnings),
        }
        return {key: value for key, value in row.items() if value not in (None, "", [], {})}


@dataclass(frozen=True)
class DraftSelection:
    draft_id: str | None
    slot: DraftSlot
    selecting_team_id: str | None
    player_id: str | None
    player_name: str | None
    selection_order: int | None = None
    selected_at: str | None = None
    lineage: list[DraftLineage] = field(default_factory=list)


@dataclass(frozen=True)
class ProspectProfile:
    prospect_id: str | None
    sleeper_id: str | None
    player_name: str | None
    position: str | None = None
    college: str | None = None
    age: float | None = None
    rookie_status: str | None = None
    draft_class: int | None = None
    stored_ranking: int | None = None
    stored_tier: str | None = None
    player_intelligence_id: str | None = None
    availability_state: str = DraftIntelligenceAvailability.PROSPECT_PROFILE_PARTIAL.value
    completeness: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    lineage: list[DraftLineage] = field(default_factory=list)


@dataclass(frozen=True)
class DraftBoardState:
    draft_season: int
    status: str = "unknown"
    current_or_next_slot: DraftSlot | None = None
    completed_selections: list[DraftSelection] = field(default_factory=list)
    remaining_slots: list[DraftSlot] = field(default_factory=list)
    team_draft_order: list[str] = field(default_factory=list)
    available_prospects: list[ProspectProfile] = field(default_factory=list)
    user_owned_picks: list[DraftPickAsset] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    lineage: list[DraftLineage] = field(default_factory=list)


@dataclass(frozen=True)
class DraftContextCompleteness:
    ownership: bool = False
    draft_order: bool = False
    selections: bool = False
    prospect_pool: bool = False
    player_profiles: bool = False
    roster_context: bool = False
    missing_groups: tuple[str, ...] = ()
    available_sources: tuple[str, ...] = ()
    unavailable_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftIntelligenceContext:
    league_id: str
    league_team_id: str
    season: int
    requested_pick: ParsedPickReference | None = None
    owned_picks: list[DraftPickAsset] = field(default_factory=list)
    requested_picks: list[DraftPickAsset] = field(default_factory=list)
    board_state: DraftBoardState | None = None
    prospect_profiles: list[ProspectProfile] = field(default_factory=list)
    roster_needs: dict[str, Any] = field(default_factory=dict)
    availability_states: list[str] = field(default_factory=list)
    completeness: DraftContextCompleteness = field(default_factory=DraftContextCompleteness)
    warnings: list[str] = field(default_factory=list)
    lineage: list[DraftLineage] = field(default_factory=list)
