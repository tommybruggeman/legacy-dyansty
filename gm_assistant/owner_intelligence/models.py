from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


OWNER_INTELLIGENCE_VERSION = "owner_intelligence.v1"


class OwnerMemoryScope(str, Enum):
    USER_GLOBAL = "user_global"
    LEAGUE = "league"
    TEAM = "team"
    CONVERSATION = "conversation"


class OwnerPreferenceSource(str, Enum):
    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    EXPLICIT_USER_CONFIRMATION = "explicit_user_confirmation"
    CURRENT_CONVERSATION = "current_conversation"
    REPEATED_BEHAVIOR_INFERENCE = "repeated_behavior_inference"
    SYSTEM_DEFAULT = "system_default"
    LEGACY_COMPATIBILITY = "legacy_compatibility"


class OwnerPreferenceStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CANDIDATE = "candidate"
    CONVERSATION_ONLY = "conversation_only"


class OwnerPreferenceCategory(str, Enum):
    STRATEGIC_GOAL = "strategic_goal"
    TIME_HORIZON = "time_horizon"
    ASSET_PREFERENCE = "asset_preference"
    RISK_TOLERANCE = "risk_tolerance"
    DECISION_STYLE = "decision_style"
    COMMUNICATION_STYLE = "communication_style"
    HARD_CONSTRAINT = "hard_constraint"


class ConfirmationState(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"


@dataclass(frozen=True)
class OwnerIntelligenceLineage:
    source_type: str
    source_ref: str | None = None
    statement: str | None = None


@dataclass(frozen=True)
class OwnerPreference:
    user_id: str
    league_id: str | None
    league_team_id: str | None
    scope: str
    category: str
    normalized_value: str
    source_type: str
    explicit: bool
    inferred: bool = False
    confidence_band: str = "high"
    status: str = OwnerPreferenceStatus.ACTIVE.value
    confirmation_state: str = ConfirmationState.NOT_REQUIRED.value
    target: str | None = None
    raw_text: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    observation_count: int | None = None
    lineage: list[OwnerIntelligenceLineage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def scope_key(self) -> tuple[str, str | None, str | None]:
        return (self.scope, self.league_id, self.league_team_id)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerGoal:
    value: str
    source_type: str
    scope: str
    confirmation_state: str = ConfirmationState.NOT_REQUIRED.value


@dataclass(frozen=True)
class OwnerConstraint:
    constraint_type: str
    value: Any
    target: str | None
    scope: str
    source_type: str
    hard: bool = True
    confirmation_state: str = ConfirmationState.NOT_REQUIRED.value


@dataclass(frozen=True)
class OwnerCommunicationPreference:
    style: str
    source_type: str
    scope: str


@dataclass(frozen=True)
class OwnerRiskPreference:
    risk_tolerance: str
    source_type: str
    scope: str
    confidence_band: str = "high"


@dataclass(frozen=True)
class OwnerStrategyState:
    strategic_goal: OwnerGoal | None = None
    time_horizon: str | None = None
    asset_preferences: list[OwnerPreference] = field(default_factory=list)
    risk_preference: OwnerRiskPreference | None = None
    hard_constraints: list[OwnerConstraint] = field(default_factory=list)
    decision_preferences: list[OwnerPreference] = field(default_factory=list)
    communication_preference: OwnerCommunicationPreference | None = None
    conversation_only: list[OwnerPreference] = field(default_factory=list)
    inferred_tendencies: list[OwnerPreference] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    confirmation_required: list[OwnerPreference] = field(default_factory=list)


@dataclass(frozen=True)
class OwnerFeedback:
    user_id: str
    league_id: str
    league_team_id: str
    feedback_type: str
    target_ref: str | None = None
    source_type: str = OwnerPreferenceSource.CURRENT_CONVERSATION.value
    creates_preference_candidate: bool = False


@dataclass(frozen=True)
class CorrectnessDispute:
    user_id: str
    league_id: str
    league_team_id: str
    disputed_claim: str
    source_ref: str | None = None
    factual_repository_mutation_allowed: bool = False


@dataclass(frozen=True)
class OwnerIntelligenceAvailability:
    explicit_preferences: str = "unavailable"
    conversation_context: str = "unavailable"
    durable_persistence: str = "unavailable"
    inferred_tendencies: str = "unavailable"
    conflicts: str = "none"
    scoped_context: str = "complete"


@dataclass(frozen=True)
class OwnerIntelligenceContext:
    user_id: str
    league_id: str
    league_team_id: str
    strategy_state: OwnerStrategyState
    active_preferences: list[OwnerPreference] = field(default_factory=list)
    temporary_preferences: list[OwnerPreference] = field(default_factory=list)
    superseded_preferences: list[OwnerPreference] = field(default_factory=list)
    feedback: list[OwnerFeedback] = field(default_factory=list)
    correctness_disputes: list[CorrectnessDispute] = field(default_factory=list)
    availability: OwnerIntelligenceAvailability = field(default_factory=OwnerIntelligenceAvailability)
    lineage: list[OwnerIntelligenceLineage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    owner_intelligence_version: str = OWNER_INTELLIGENCE_VERSION

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def to_legacy_owner_preferences(self) -> dict[str, Any]:
        notes: list[str] = []
        for constraint in self.strategy_state.hard_constraints:
            if constraint.constraint_type == "do_not_trade_first_round_pick":
                notes.append("explicit_preference:do_not_trade_first_round_pick")
            elif constraint.constraint_type == "do_not_trade_player" and constraint.target:
                notes.append(f"explicit_preference:do_not_trade_player:{constraint.target}")
        for pref in self.strategy_state.asset_preferences:
            notes.append(f"explicit_preference:{pref.normalized_value}")
        out: dict[str, Any] = {"notes": notes}
        if self.strategy_state.strategic_goal:
            out["team_build_preference"] = self.strategy_state.strategic_goal.value
        if self.strategy_state.risk_preference:
            out["risk_tolerance"] = self.strategy_state.risk_preference.risk_tolerance
        if self.strategy_state.communication_preference:
            out["communication_style"] = self.strategy_state.communication_preference.style
        return out
