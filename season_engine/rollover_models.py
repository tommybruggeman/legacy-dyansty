from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class ReadinessStatus(str, Enum):
    READY = "ready"
    CONDITIONALLY_READY = "conditionally_ready"
    BLOCKED = "blocked"
    COMMISSIONER_DECISION_REQUIRED = "commissioner_decision_required"
    SCHEMA_REQUIRED = "schema_required"
    AUTHORITY_AMBIGUOUS = "authority_ambiguous"


class CapAuthorityKind(str, Enum):
    AUTHORITATIVE_CURRENT = "authoritative_current"
    PROJECTED_TARGET = "projected_target"
    HISTORICAL = "historical"
    TRANSITION_READY = "transition_ready"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SeasonRolloverContext:
    league_id: str
    source_league_season: int
    target_league_season: int
    league_season_status: str
    contract_operational_season: int
    cap_authority_season: int
    roster_snapshot_season: int
    free_agent_publication_season: int | None
    draft_authority_season: int | None
    historical_capture_status: str
    contract_transition_status: str
    cap_transition_status: str
    roster_transition_status: str
    free_agent_transition_status: str
    draft_transition_status: str
    readiness_status: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    provenance: dict[str, Any]
    source_fingerprint: str
    plan_fingerprint: str


@dataclass(frozen=True)
class CapSeasonPolicy:
    authoritative_season: int
    target_season: int
    current_kind: CapAuthorityKind
    target_kind: CapAuthorityKind
    salary_cap_limit: Decimal | None
    adjustments_season_scoped: bool
    dead_cap_season_scoped: bool
    authority_change_event: str
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectedTeamCap:
    league_team_id: str
    team_name: str
    target_season: int
    active_contract_salary: Decimal
    cap_adjustments: Decimal | None
    dead_cap: Decimal | None
    total_committed_salary: Decimal | None
    salary_cap_limit: Decimal | None
    available_cap: Decimal | None
    complete: bool
    provenance: tuple[str, ...]
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FreeAgentPublicationState:
    player_id: str
    contract_unbound: bool
    unrostered: bool
    published: bool | None
    acquisition_eligible: bool | None
    waiver_locked: bool | None
    rookie_not_yet_available: bool | None
    commissioner_hold: bool | None
    authority: str
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommissionerRolloverDecision:
    decision_id: str
    league_id: str
    player_id: str | None
    team_id: str | None
    category: str
    current_state: str
    proposed_action: str
    allowed_actions: tuple[str, ...]
    recommended_action: str | None
    evidence: dict[str, Any]
    rule_source: str | None
    uncertainty: str
    blocking: bool
    decision_required_before: str
    provenance: tuple[str, ...]


@dataclass(frozen=True)
class ContractRosterException:
    agreement_id: str
    player_id: str
    player_name: str
    team_id: str
    roster_status: str
    contract_status: str
    classification: str
    proposed_action: str
    taxi_or_ir: str | None
    publication_status: str
    commissioner_decision_id: str | None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequirementNode:
    node_id: str
    prerequisites: tuple[str, ...]
    blocking: bool
    irreversible: bool
    retry_key_required: bool
    authority_change: str | None
    validation_checkpoint: str


@dataclass(frozen=True)
class RolloverReadinessReport:
    context: SeasonRolloverContext
    cap_policy: CapSeasonPolicy
    projected_caps: tuple[ProjectedTeamCap, ...]
    publication_authority: str
    roster_exceptions: tuple[ContractRosterException, ...]
    commissioner_decisions: tuple[CommissionerRolloverDecision, ...]
    requirement_graph: tuple[RequirementNode, ...]
    page_readiness: tuple[dict[str, Any], ...]
    write_path_readiness: tuple[dict[str, Any], ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    recommended_next_phase: str
    source_fingerprint: str
    plan_fingerprint: str
    writes_performed: int = 0
