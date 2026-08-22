from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol


REASONING_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "answer_type": {"type": "string", "enum": ["factual_explanation", "recommendation", "comparison", "scenario_analysis", "clarification_required", "insufficient_evidence", "unsupported"]},
        "direct_answer": {"type": "string"},
        "recommendation": {"type": ["string", "null"]},
        "recommendation_strength": {"type": "string", "enum": ["strong", "moderate", "slight", "none"]},
        "key_reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "main_risks": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "strategic_purpose": {"type": "string"},
                    "required_verified_assets": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "unverified_assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                    "requires_deterministic_simulation": {"type": "boolean"},
                },
                "required": ["label", "description", "strategic_purpose", "required_verified_assets", "unverified_assumptions", "requires_deterministic_simulation"],
            },
            "maxItems": 3,
        },
        "ranked_players": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "rank": {"type": "integer", "minimum": 1, "maximum": 24},
                    "player_name": {"type": "string"},
                    "player_id": {"type": "string"},
                    "short_reason": {"type": "string"},
                    "fact_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                },
                "required": ["rank", "player_name", "player_id", "short_reason", "fact_refs"],
            },
            "maxItems": 24,
        },
        "clarifying_question": {"type": ["string", "null"]},
        "facts_used": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "limitations": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "constraint_conflicts": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "requires_deterministic_follow_up": {"type": "boolean"},
    },
    "required": ["answer_type", "direct_answer", "recommendation", "recommendation_strength", "key_reasons", "main_risks", "alternatives", "ranked_players", "clarifying_question", "facts_used", "limitations", "constraint_conflicts", "requires_deterministic_follow_up"],
}


class ReasoningAnswerType(str, Enum):
    FACTUAL_EXPLANATION = "factual_explanation"
    RECOMMENDATION = "recommendation"
    COMPARISON = "comparison"
    SCENARIO_ANALYSIS = "scenario_analysis"
    CLARIFICATION_REQUIRED = "clarification_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED = "unsupported"


class RecommendationStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    SLIGHT = "slight"
    NONE = "none"


@dataclass(frozen=True)
class ReasoningAlternative:
    label: str
    description: str
    strategic_purpose: str
    required_verified_assets: list[str] = field(default_factory=list)
    unverified_assumptions: list[str] = field(default_factory=list)
    requires_deterministic_simulation: bool = False


@dataclass(frozen=True)
class ReasoningRankedPlayer:
    rank: int
    player_name: str
    player_id: str
    short_reason: str
    fact_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReasoningResponse:
    answer_type: str
    direct_answer: str
    recommendation: str | None = None
    recommendation_strength: str = RecommendationStrength.NONE.value
    key_reasons: list[str] = field(default_factory=list)
    main_risks: list[str] = field(default_factory=list)
    alternatives: list[ReasoningAlternative] = field(default_factory=list)
    ranked_players: list[ReasoningRankedPlayer] = field(default_factory=list)
    clarifying_question: str | None = None
    facts_used: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    constraint_conflicts: list[str] = field(default_factory=list)
    requires_deterministic_follow_up: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReasoningRequest:
    request_id: str
    league_id: str
    league_team_id: str
    normalized_intent: str
    normalized_objective: str
    user_question: str
    conversation_context: list[dict[str, str]] = field(default_factory=list)
    verified_facts: dict[str, Any] = field(default_factory=dict)
    deterministic_calculations: dict[str, Any] = field(default_factory=dict)
    scenario_result: dict[str, Any] = field(default_factory=dict)
    football_intelligence: dict[str, Any] = field(default_factory=dict)
    owner_intelligence: dict[str, Any] = field(default_factory=dict)
    league_owner_intelligence: dict[str, Any] = field(default_factory=dict)
    draft_intelligence: dict[str, Any] = field(default_factory=dict)
    player_intelligence: dict[str, Any] = field(default_factory=dict)
    validation_constraints: dict[str, Any] = field(default_factory=dict)
    known_missing_evidence: list[str] = field(default_factory=list)
    permitted_recommendation_scope: str = "explain_validated_recommendation_only"
    desired_communication_style: str | None = None
    allowed_fact_refs: list[str] = field(default_factory=list)
    safe_lineage_refs: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReasoningTrace:
    request_id: str
    provider_status: str
    eligibility_decision: str
    provider_selected: str | None = None
    provider_called: bool = False
    provider_skipped_reason: str | None = None
    result_status: str | None = None
    schema_parse_status: str | None = None
    model_label: str | None = None
    prompt_section_count: int = 0
    evidence_domains_included: list[str] = field(default_factory=list)
    response_type: str | None = None
    validation_status: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    fallback_status: str | None = None
    fallback_reason: str | None = None
    final_answer_source: str | None = None
    latency_ms: int = 0
    safe_error_code: str | None = None
    provider_error_details: dict[str, Any] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReasoningConfigurationStatus:
    reasoning_enabled: bool
    api_key_present: bool
    provider_selected: str
    model_configured: bool
    timeout_configured: bool
    max_output_tokens_configured: bool
    live_testing_permitted: bool
    configuration_valid: bool
    safe_error_code: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    response: ReasoningResponse | None = None
    trace: ReasoningTrace | None = None
    error_code: str | None = None


class ReasoningProvider(Protocol):
    def reason(self, request: ReasoningRequest) -> ProviderResult:
        ...
