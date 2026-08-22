from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable

from gm_assistant.conversation_state import ConversationState
from gm_assistant.interpretation import InterpretedQuestion, Intent, ResolutionStatus, is_football_intelligence_question, is_league_owner_intelligence_question, is_roster_list_question
from gm_assistant.objective import Goal, OwnerObjective
from gm_assistant.request_context import AssistantRequestContext


PLANNER_VERSION = "gm_decision_planner.v1"
MAX_RETRIEVAL_REQUESTS = 10
MAX_LEAGUE_WIDE_RETRIEVALS = 2
MAX_TARGET_COUNT = 12
MAX_DEFAULT_SEASONS = 3


class PlanType(str, Enum):
    FACTUAL_LOOKUP = "factual_lookup_plan"
    RULES_LOOKUP = "rules_lookup_plan"
    PLAYER_EVALUATION = "player_evaluation_plan"
    PLAYER_COMPARISON = "player_comparison_plan"
    ROSTER_EVALUATION = "roster_evaluation_plan"
    TRADE_EVALUATION = "trade_evaluation_plan"
    TRADE_DISCOVERY = "trade_discovery_plan"
    TRADE_CONSTRUCTION = "trade_construction_plan"
    DRAFT_RECOMMENDATION = "draft_recommendation_plan"
    DRAFT_PICK_EVALUATION = "draft_pick_evaluation_plan"
    FREE_AGENT = "free_agent_plan"
    CONTRACT = "contract_plan"
    SALARY_CAP = "salary_cap_plan"
    LINEUP = "lineup_plan"
    ROSTER_MOVE = "roster_move_plan"
    LONG_TERM_PLANNING = "long_term_planning_plan"
    TEAM_COMPARISON = "team_comparison_plan"
    LEAGUE_ANALYSIS = "league_analysis_plan"
    SCENARIO_SIMULATION = "scenario_simulation_plan"
    GENERAL_CONVERSATION = "general_conversation_plan"
    UNSUPPORTED = "unsupported_plan"
    BLOCKED = "blocked_plan"


class ResponseMode(str, Enum):
    DIRECT_FACTUAL = "direct_factual"
    RULES_EXPLANATION = "rules_explanation"
    STRUCTURED_EVALUATION = "structured_evaluation"
    RANKED_RECOMMENDATIONS = "ranked_recommendations"
    SCENARIO_COMPARISON = "scenario_comparison"
    TRANSACTION_CONSTRUCTION = "transaction_construction"
    LIMITED_ANSWER = "limited_answer"
    CLARIFICATION_REQUIRED = "clarification_required"
    GENERAL_CONVERSATION = "general_conversation"
    UNSUPPORTED = "unsupported"


class DecisionEngine(str, Enum):
    FACTUAL_LOOKUP = "factual_lookup_engine"
    RULES_EXPLANATION = "rules_explanation_engine"
    PLAYER_EVALUATION = "player_evaluation_engine"
    PLAYER_COMPARISON = "player_comparison_engine"
    ROSTER_EVALUATION = "roster_evaluation_engine"
    TRADE_EVALUATION = "trade_evaluation_engine"
    TRADE_DISCOVERY = "trade_discovery_engine"
    TRADE_CONSTRUCTION = "trade_construction_engine"
    DRAFT_RECOMMENDATION = "draft_recommendation_engine"
    DRAFT_PICK_EVALUATION = "draft_pick_evaluation_engine"
    FREE_AGENT = "free_agent_engine"
    CONTRACT = "contract_engine"
    SALARY_CAP = "salary_cap_engine"
    LINEUP = "lineup_engine"
    ROSTER_MOVE = "roster_move_engine"
    LONG_TERM_PLANNING = "long_term_planning_engine"
    TEAM_COMPARISON = "team_comparison_engine"
    LEAGUE_ANALYSIS = "league_analysis_engine"
    SCENARIO_SIMULATION = "scenario_simulation_engine"
    CONVERSATION = "conversation_engine"
    UNSUPPORTED = "unsupported_engine"


class FallbackStrategy(str, Enum):
    NONE = "none"
    FACTUAL_ONLY = "factual_only"
    INTERNAL_DATA_ONLY = "internal_data_only"
    LIMITED_WITHOUT_EXTERNAL_DATA = "limited_without_external_data"
    CONDITIONAL_ON_RULES = "conditional_on_rules"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class RetrievalRequest:
    retrieval_type: str
    scope: str
    entity_ids: list[str] = field(default_factory=list)
    team_ids: list[str] = field(default_factory=list)
    player_ids: list[str] = field(default_factory=list)
    pick_ids: list[str] = field(default_factory=list)
    seasons: list[int] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    required: bool = True
    reason: str = ""
    freshness_requirement: str | None = None


@dataclass(frozen=True)
class RuleRequest:
    rule_type: str
    season: int | None = None
    required: bool = True
    reason: str = ""
    related_entity_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CalculationRequest:
    calculation_type: str
    required: bool = True
    reason: str = ""
    input_refs: list[str] = field(default_factory=list)
    output_key: str = ""


@dataclass(frozen=True)
class ValidationStep:
    validation_type: str
    required: bool = True
    reason: str = ""
    blocking_on_failure: bool = True


@dataclass(frozen=True)
class PlanEntity:
    entity_type: str
    canonical_id: str | None
    raw_text: str | None
    required: bool
    resolved: bool


@dataclass(frozen=True)
class PlanBlocker:
    blocker_type: str
    explanation: str
    source_ref: str | None = None
    resolvable: bool = True


@dataclass(frozen=True)
class DecisionPlan:
    plan_type: str
    request_goal: str
    active_strategic_goal: str | None
    decision_engine: str
    response_mode: str
    retrieval_requests: list[RetrievalRequest] = field(default_factory=list)
    rule_requests: list[RuleRequest] = field(default_factory=list)
    calculation_requests: list[CalculationRequest] = field(default_factory=list)
    validation_steps: list[ValidationStep] = field(default_factory=list)
    required_entities: list[PlanEntity] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    optional_inputs: list[str] = field(default_factory=list)
    blockers: list[PlanBlocker] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unnecessary_data: list[str] = field(default_factory=list)
    fallback_strategy: str | None = FallbackStrategy.NONE.value
    ready_for_execution: bool = True
    confidence: str = "medium"
    planner_version: str = PLANNER_VERSION

    def to_packet(self) -> dict[str, Any]:
        return asdict(self)


PlanHandler = Callable[[AssistantRequestContext, ConversationState | None, InterpretedQuestion, OwnerObjective], DecisionPlan]


def build_decision_plan(
    *,
    context: AssistantRequestContext,
    conversation_state: ConversationState | None,
    interpreted_question: InterpretedQuestion,
    owner_objective: OwnerObjective,
) -> DecisionPlan:
    base_blockers = _base_blockers(context, interpreted_question, owner_objective)
    if interpreted_question.primary_intent == Intent.UNSUPPORTED.value:
        return _finalize_plan(_unsupported_plan(context, interpreted_question, owner_objective, base_blockers))
    if base_blockers:
        return _finalize_plan(_blocked_plan(context, interpreted_question, owner_objective, base_blockers))
    handler = PLANNER_HANDLERS.get(interpreted_question.primary_intent, _general_conversation_plan)
    plan = handler(context, conversation_state, interpreted_question, owner_objective)
    return _finalize_plan(plan)


def build_plan_packet(plan: DecisionPlan | None) -> dict[str, Any]:
    if not plan:
        return {}
    packet = plan.to_packet()
    packet["retrieval_requests"] = [
        {
            "retrieval_type": item["retrieval_type"],
            "scope": item["scope"],
            "team_ids": item["team_ids"],
            "player_ids": item["player_ids"],
            "pick_ids": item["pick_ids"],
            "seasons": item["seasons"],
            "filters": item["filters"],
            "required": item["required"],
            "reason": item["reason"],
        }
        for item in packet.get("retrieval_requests", [])
    ]
    return packet


def _factual_lookup_plan(
    context: AssistantRequestContext,
    _state: ConversationState | None,
    interpreted: InterpretedQuestion,
    objective: OwnerObjective,
) -> DecisionPlan:
    normalized = interpreted.raw_question.lower()
    if is_player_evaluation_roster_question(normalized):
        retrievals = [
            _retrieval("player_evaluations", "team", context, team_ids=[context.league_team_id], reason="Evaluate every rostered player from scoped verified player, contract, and roster data."),
        ]
        validations = _common_validations() + [_validation("team_scope", "Verify player evaluations are scoped to the authenticated league team.")]
        return _plan(PlanType.FACTUAL_LOOKUP, objective, DecisionEngine.FACTUAL_LOOKUP, ResponseMode.DIRECT_FACTUAL, retrievals, validations, unnecessary=["external_rankings", "injury_news", "draft_pick_values", "trade_realism"])
    if is_league_owner_intelligence_question(normalized):
        retrievals = [_retrieval("current_user_context", "team", context, team_ids=[context.league_team_id], reason="Answer factual league-owner intelligence question from scoped context.")]
        validations = _common_validations() + [_validation("league_scope", "Verify league-owner intelligence is scoped to the authenticated league.")]
        return _plan(PlanType.FACTUAL_LOOKUP, objective, DecisionEngine.FACTUAL_LOOKUP, ResponseMode.DIRECT_FACTUAL, retrievals, validations, unnecessary=["strategic_recommendation", "trade_acceptance_prediction", "psychological_profile"])
    if is_football_intelligence_question(normalized):
        retrievals = [
            _retrieval("team_roster", "team", context, team_ids=[context.league_team_id], reason="Load scoped roster rows for deterministic football context."),
            _retrieval("team_contracts", "team", context, team_ids=[context.league_team_id], reason="Load scoped contracts for deterministic football context."),
            _retrieval("cap_summary", "team", context, team_ids=[context.league_team_id], reason="Load scoped cap facts for deterministic football context."),
            _retrieval("draft_picks", "team", context, team_ids=[context.league_team_id], reason="Load verified draft assets for deterministic football context."),
            _retrieval("league_rules", "league", context, seasons=_seasons(context, interpreted), reason="Load structured lineup settings for deterministic football context."),
        ]
        validations = _common_validations() + [_validation("team_scope", "Verify football intelligence is scoped to the authenticated league team.")]
        return _plan(PlanType.FACTUAL_LOOKUP, objective, DecisionEngine.FACTUAL_LOOKUP, ResponseMode.DIRECT_FACTUAL, retrievals, validations, unnecessary=["external_rankings", "projections", "injury_news", "strategic_recommendation"])
    if is_roster_list_question(normalized) or _has_any(normalized, ("who is on the roster", "who are my players")):
        retrievals = [_retrieval("team_roster", "team", context, team_ids=[context.league_team_id], reason="List verified active roster players.")]
        validations = _common_validations() + [_validation("roster_scope", "Verify roster rows belong to the active team.")]
        return _plan(PlanType.FACTUAL_LOOKUP, objective, DecisionEngine.FACTUAL_LOOKUP, ResponseMode.DIRECT_FACTUAL, retrievals, validations, unnecessary=["strategic_calculations", "trade_history", "market_values"])
    if interpreted.pick_refs:
        retrievals = [_retrieval("draft_pick", "league", context, pick_ids=_pick_labels(interpreted), seasons=_seasons(context, interpreted), reason="Resolve requested pick ownership.")]
        validations = _common_validations() + [_validation("pick_identity", "Verify pick round, slot, season, and current owner.")]
        return _plan(PlanType.FACTUAL_LOOKUP, objective, DecisionEngine.FACTUAL_LOOKUP, ResponseMode.DIRECT_FACTUAL, retrievals, validations, unnecessary=["all_rosters", "market_values", "trade_history", "projections"])
    if interpreted.player_refs:
        player_ids = _player_ids(interpreted)
        retrievals = [
            _retrieval("player_profile", "league", context, player_ids=player_ids, reason="Resolve factual player identity."),
            _retrieval("player_contract", "league", context, player_ids=player_ids, reason="Return contract facts when available."),
        ]
        validations = _common_validations() + [_validation("entity_existence", "Verify the requested player exists in the active league.")]
        return _plan(PlanType.FACTUAL_LOOKUP, objective, DecisionEngine.FACTUAL_LOOKUP, ResponseMode.DIRECT_FACTUAL, retrievals, validations, unnecessary=["all_teams", "league_history", "draft_capital", "projections"])
    retrievals = [_retrieval("current_user_context", "team", context, team_ids=[context.league_team_id], reason="Answer scoped factual context question.")]
    return _plan(PlanType.FACTUAL_LOOKUP, objective, DecisionEngine.FACTUAL_LOOKUP, ResponseMode.DIRECT_FACTUAL, retrievals, _common_validations(), unnecessary=["market_values", "trade_history", "projections"])


def is_player_evaluation_roster_question(normalized_question: str) -> bool:
    normalized = str(normalized_question or "").strip().lower().rstrip("?")
    if any(
        marker in normalized
        for marker in (
            "who is my best player",
            "who's my best player",
            "who are my best players",
            "who are my three best players",
            "who are my 3 best players",
            "who are the best players on my roster",
            "top three players",
            "top 3 players",
            "top five players",
            "top 5 players",
            "strongest players",
            "order my roster by value",
            "best-to-worst roster ranking",
            "best to worst roster ranking",
        )
    ):
        return True
    if "rank" in normalized and any(term in normalized for term in ("roster", "players", "team")):
        return True
    if "best" in normalized and any(term in normalized for term in ("players on my roster", "players on the roster", "my players")):
        return True
    if "show me" in normalized and any(term in normalized for term in ("top", "best", "strongest")) and "players" in normalized:
        return True
    if "list" in normalized and "strongest" in normalized and "players" in normalized:
        return True
    return False


def _rules_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    player_ids = _player_ids(interpreted)
    retrievals = [_retrieval("league_rules", "league", context, seasons=_seasons(context, interpreted), reason="Load the applicable league rules.")]
    if player_ids:
        retrievals.append(_retrieval("player_roster_status", "team", context, player_ids=player_ids, reason="Check player status required for eligibility."))
    rules = []
    raw = interpreted.raw_question.lower()
    if "taxi" in raw:
        rules.append(_rule("taxi_eligibility", context.requested_season, "Check taxi eligibility."))
    if "deadline" in raw:
        rules.append(_rule("trade_deadline", context.requested_season, "Check league trade deadline."))
    if "roster" in raw:
        rules.append(_rule("roster_size_limit", context.requested_season, "Check roster-size legality."))
    if "cap" in raw or "afford" in raw:
        rules.append(_rule("salary_cap_legality", context.requested_season, "Check salary-cap legality."))
    if not rules:
        rules.append(_rule("league_rule_lookup", context.requested_season, "Explain applicable league rule."))
    validations = _common_validations() + [_validation("rule_legality", "Validate that the requested action is legal under league settings.")]
    return _plan(PlanType.RULES_LOOKUP, objective, DecisionEngine.RULES_EXPLANATION, ResponseMode.RULES_EXPLANATION, retrievals, validations, rules=rules, fallback=FallbackStrategy.CONDITIONAL_ON_RULES.value, unnecessary=["player_market_rankings", "league_wide_trade_history", "owner_tendencies"])


def _scenario_simulation_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    retrievals = [
        _retrieval(
            "scenario_simulation",
            "team",
            context,
            team_ids=[context.league_team_id],
            filters={"raw_question": interpreted.raw_question},
            reason="Run a read-only hypothetical scenario against the authenticated team's scoped roster, cap, contracts, and draft picks.",
        )
    ]
    validations = _common_validations() + [
        _validation("scenario_read_only", "Verify the scenario is simulated only and no transaction is executed."),
        _validation("team_scope", "Verify scenario inputs apply only to the authenticated league team."),
    ]
    return _plan(
        PlanType.SCENARIO_SIMULATION,
        objective,
        DecisionEngine.SCENARIO_SIMULATION,
        ResponseMode.DIRECT_FACTUAL,
        retrievals,
        validations,
        fallback=FallbackStrategy.FACTUAL_ONLY.value,
        unnecessary=["recommendation_engine", "trade_execution", "database_mutation", "league_wide_rosters"],
    )


def _player_evaluation_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    blockers = _require_resolved_players(interpreted, minimum=1)
    player_ids = _player_ids(interpreted)
    retrievals = [
        _retrieval("player_profile", "league", context, player_ids=player_ids, reason="Load player identity and profile."),
        _retrieval("player_contract", "league", context, player_ids=player_ids, reason="Load contract context."),
        _retrieval("league_relative_value", "league", context, player_ids=player_ids, reason="Load scoped value context."),
        _retrieval("team_fit_context", "team", context, team_ids=[context.league_team_id], player_ids=player_ids, required=False, reason="Support fit against the owner's objective."),
    ]
    calculations = [
        _calc("contract_efficiency", "Compare production/value to salary and years."),
        _calc("age_profile", "Estimate age trajectory against timeframe."),
        _calc("competitive_window_fit", "Fit player to active strategic goal."),
        _calc("risk_profile", "Identify risk signals."),
    ]
    validations = _common_validations() + [_validation("entity_existence", "Verify player identity."), _validation("data_freshness", "Check player data freshness.", blocking=False)]
    return _plan(PlanType.PLAYER_EVALUATION, objective, DecisionEngine.PLAYER_EVALUATION, ResponseMode.STRUCTURED_EVALUATION, retrievals, validations, calculations=calculations, blockers=blockers, fallback=FallbackStrategy.LIMITED_WITHOUT_EXTERNAL_DATA.value, unnecessary=["all_league_rosters", "full_trade_history"])


def _player_comparison_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    blockers = _require_resolved_players(interpreted, minimum=2)
    player_ids = _player_ids(interpreted)
    retrievals = [
        _retrieval("player_profile", "league", context, player_ids=player_ids, reason="Load equivalent profile evidence for all compared players."),
        _retrieval("player_contract", "league", context, player_ids=player_ids, reason="Load equivalent contract evidence."),
        _retrieval("league_relative_value", "league", context, player_ids=player_ids, reason="Load scoped value evidence."),
        _retrieval("team_fit_context", "team", context, team_ids=[context.league_team_id], player_ids=player_ids, required=False, reason="Compare fit against owner objective."),
    ]
    calculations = [_calc("value_comparison", "Compare player values."), _calc("contract_efficiency_comparison", "Compare contract efficiency."), _calc("risk_comparison", "Compare risk."), _calc("timeframe_fit", "Compare fit to requested timeframe.")]
    validations = _common_validations() + [_validation("entity_existence", "Verify all compared players exist."), _validation("same_league_scope", "Ensure comparison stays within active league.")]
    return _plan(PlanType.PLAYER_COMPARISON, objective, DecisionEngine.PLAYER_COMPARISON, ResponseMode.SCENARIO_COMPARISON, retrievals, validations, calculations=calculations, blockers=blockers, fallback=FallbackStrategy.LIMITED_WITHOUT_EXTERNAL_DATA.value, unnecessary=["all_transactions", "all_draft_picks"])


def _roster_evaluation_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    retrievals = [
        _retrieval("team_roster", "team", context, team_ids=[context.league_team_id], reason="Load canonical team roster."),
        _retrieval("team_contracts", "team", context, team_ids=[context.league_team_id], reason="Load team contracts."),
        _retrieval("cap_summary", "team", context, team_ids=[context.league_team_id], reason="Load cap summary."),
        _retrieval("draft_picks", "team", context, team_ids=[context.league_team_id], seasons=_seasons(context, interpreted), reason="Load owned draft capital."),
        _retrieval("team_brain", "team", context, team_ids=[context.league_team_id], reason="Load compact team brain."),
    ]
    calculations = [_calc("roster_depth", "Evaluate positional depth."), _calc("positional_need", "Identify needs and surplus."), _calc("cap_flexibility", "Assess cap flexibility."), _calc("competitive_window_fit", "Assess team window."), _calc("draft_capital_strength", "Assess future pick strength.")]
    return _plan(PlanType.ROSTER_EVALUATION, objective, DecisionEngine.ROSTER_EVALUATION, ResponseMode.STRUCTURED_EVALUATION, retrievals, _common_validations(), calculations=calculations, fallback=FallbackStrategy.INTERNAL_DATA_ONLY.value, unnecessary=["weekly_lineup_projections", "full_league_history"])


def _trade_evaluation_plan(context: AssistantRequestContext, state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    blockers = []
    assets = interpreted.included_assets or []
    if len(assets) < 2 and not (state and state.current_scenario):
        blockers.append(_blocker("incomplete_trade_sides", "Trade evaluation needs both outgoing and incoming assets.", "included_assets"))
    blockers.extend(_require_resolved_trade_assets(interpreted))
    player_ids = _player_ids(interpreted)
    team_ids = _team_ids(interpreted, context)
    retrievals = [
        _retrieval("player_profile", "league", context, player_ids=player_ids, reason="Load all named player profiles."),
        _retrieval("player_contract", "league", context, player_ids=player_ids, reason="Load contracts for trade assets."),
        _retrieval("asset_ownership", "league", context, player_ids=player_ids, pick_ids=_pick_labels(interpreted), reason="Verify player and pick ownership."),
        _retrieval("team_roster", "team", context, team_ids=_bounded_team_ids([context.league_team_id] + team_ids), reason="Load relevant team rosters."),
        _retrieval("cap_summary", "team", context, team_ids=_bounded_team_ids([context.league_team_id] + team_ids), reason="Check cap impact."),
        _retrieval("league_rules", "league", context, seasons=_seasons(context, interpreted), reason="Check trade rules."),
    ]
    rules = [_rule("salary_cap_legality", context.requested_season, "Trade must fit cap rules."), _rule("roster_size_limit", context.requested_season, "Trade must preserve legal roster size."), _rule("pick_tradeability", context.requested_season, "Pick assets must be tradeable.")]
    calculations = [_calc("trade_value_delta", "Compare asset value delta."), _calc("contract_delta", "Compare contract obligations."), _calc("cap_impact", "Estimate cap impact."), _calc("roster_impact", "Estimate roster impact."), _calc("trade_realism", "Assess future realism.")]
    validations = _common_validations() + [_validation("asset_ownership", "Verify outgoing assets are owned."), _validation("pick_availability", "Verify included picks are available."), _validation("rule_legality", "Validate trade legality."), _validation("cap_math", "Validate cap impact."), _validation("roster_size_impact", "Validate roster impact."), _validation("excluded_assets", "Ensure excluded assets are not included.")]
    return _plan(PlanType.TRADE_EVALUATION, objective, DecisionEngine.TRADE_EVALUATION, ResponseMode.STRUCTURED_EVALUATION, retrievals, validations, rules=rules, calculations=calculations, blockers=blockers, fallback=FallbackStrategy.CLARIFICATION_REQUIRED.value, unnecessary=["full_league_history", "unrelated_owner_memory"])


def _trade_discovery_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    count = interpreted.requested_count or 5
    warnings = []
    if count > MAX_TARGET_COUNT:
        warnings.append(f"requested_count_capped_to_{MAX_TARGET_COUNT}")
        count = MAX_TARGET_COUNT
    retrievals = [
        _retrieval("team_roster", "team", context, team_ids=[context.league_team_id], reason="Load owner assets and needs."),
        _retrieval("draft_picks", "team", context, team_ids=[context.league_team_id], seasons=_seasons(context, interpreted), reason="Load own pick availability."),
        _retrieval("league_rosters", "league_wide", context, filters={"positions": interpreted.positions, "limit": count}, reason="Find possible targets in active league."),
        _retrieval("cap_summaries", "league_wide", context, filters={"limit": count}, required=False, reason="Support affordability checks."),
        _retrieval("team_brain", "team", context, team_ids=[context.league_team_id], reason="Load team needs."),
    ]
    calculations = [_calc("candidate_fit", "Score target fit."), _calc("affordability", "Estimate affordability."), _calc("holder_motivation", "Estimate whether holder may move target."), _calc("trade_realism", "Estimate realistic target tier."), _calc("age_filter", "Apply age constraints."), _calc("contract_fit", "Apply contract constraints.")]
    validations = _common_validations() + [_validation("target_ownership", "Verify current target ownership."), _validation("excluded_assets", "Protect excluded assets."), _validation("requested_count", "Respect requested count within bounds.")]
    return _plan(PlanType.TRADE_DISCOVERY, objective, DecisionEngine.TRADE_DISCOVERY, ResponseMode.RANKED_RECOMMENDATIONS, retrievals, validations, calculations=calculations, warnings=warnings, fallback=FallbackStrategy.INTERNAL_DATA_ONLY.value, unnecessary=["all_transactions", "all_projections_without_filters"])


def _trade_construction_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    blockers = _require_resolved_players(interpreted, minimum=1)
    player_ids = _player_ids(interpreted)
    retrievals = [
        _retrieval("player_profile", "league", context, player_ids=player_ids, reason="Load target profile."),
        _retrieval("asset_ownership", "league", context, player_ids=player_ids, reason="Find target owner."),
        _retrieval("team_roster", "team", context, team_ids=[context.league_team_id], reason="Load owner assets."),
        _retrieval("draft_picks", "team", context, team_ids=[context.league_team_id], seasons=_seasons(context, interpreted), reason="Load owner picks."),
        _retrieval("counterparty_context", "league", context, player_ids=player_ids, required=False, reason="Load target owner's roster context after ownership is known."),
        _retrieval("cap_summary", "team", context, team_ids=[context.league_team_id], reason="Check cap room."),
    ]
    calculations = [_calc("target_value", "Estimate target value."), _calc("offer_value", "Estimate offer value."), _calc("trade_realism", "Assess realism."), _calc("bilateral_fit", "Assess fit for both sides."), _calc("cap_impact", "Estimate cap impact."), _calc("roster_impact", "Estimate roster impact.")]
    validations = _common_validations() + [_validation("target_ownership", "Verify target owner."), _validation("offer_asset_ownership", "Verify offer assets."), _validation("excluded_assets", "Honor excluded assets."), _validation("pick_availability", "Verify pick availability."), _validation("rule_legality", "Validate legality.")]
    return _plan(PlanType.TRADE_CONSTRUCTION, objective, DecisionEngine.TRADE_CONSTRUCTION, ResponseMode.TRANSACTION_CONSTRUCTION, retrievals, validations, calculations=calculations, blockers=blockers, fallback=FallbackStrategy.CLARIFICATION_REQUIRED.value, unnecessary=["unbounded_trade_history"])


def _draft_recommendation_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    blockers = []
    if not interpreted.pick_refs:
        blockers.append(_blocker("missing_requested_pick", "Draft recommendation needs a pick or slot.", "pick_refs"))
    retrievals = [
        _retrieval("draft_order", "league", context, seasons=_seasons(context, interpreted), reason="Load draft order."),
        _retrieval("draft_pick", "league", context, pick_ids=_pick_labels(interpreted), seasons=_seasons(context, interpreted), reason="Verify pick ownership and slot."),
        _retrieval("prospect_pool", "league", context, seasons=_seasons(context, interpreted), required=False, reason="Load eligible prospect pool when a verified internal prospect source exists."),
        _retrieval("team_needs", "team", context, team_ids=[context.league_team_id], reason="Load roster needs."),
        _retrieval("rookie_contract_rules", "league", context, seasons=_seasons(context, interpreted), required=False, reason="Load rookie contract rules."),
    ]
    calculations = [
        _calc("best_player_available", "Compare prospect tiers when verified prospect evidence exists.", required=False),
        _calc("need_fit", "Score need fit when prospect and team-need evidence exists.", required=False),
        _calc("positional_scarcity", "Assess scarcity when prospect evidence exists.", required=False),
        _calc("long_term_value", "Assess long-term value when verified prospect value exists.", required=False),
        _calc("trade_down_opportunity", "Assess trade-down value when verified pick/prospect evidence exists.", required=False),
    ]
    validations = _common_validations() + [_validation("pick_ownership", "Verify pick belongs to owner when relevant."), _validation("prospect_eligibility", "Verify prospects are eligible."), _validation("prospect_data_availability", "Check prospect data availability.", blocking=False)]
    return _plan(PlanType.DRAFT_RECOMMENDATION, objective, DecisionEngine.DRAFT_RECOMMENDATION, ResponseMode.RANKED_RECOMMENDATIONS, retrievals, validations, calculations=calculations, blockers=blockers, warnings=["future_prospect_source_required"], fallback=FallbackStrategy.LIMITED_WITHOUT_EXTERNAL_DATA.value, unnecessary=["veteran_trade_history"])


def _draft_pick_evaluation_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    blockers = []
    if not interpreted.pick_refs:
        blockers.append(_blocker("missing_requested_pick", "Pick evaluation needs a pick reference.", "pick_refs"))
    if interpreted.pick_refs and not any(p.current_owner_team_id or p.slot for p in interpreted.pick_refs):
        blockers.append(_blocker("unresolved_pick_owner", "Pick ownership is unresolved.", "pick_refs"))
    retrievals = [
        _retrieval("draft_pick", "league", context, pick_ids=_pick_labels(interpreted), seasons=_seasons(context, interpreted), reason="Verify pick identity and ownership."),
        _retrieval("projected_team_strength", "league", context, required=False, reason="Estimate likely pick range."),
        _retrieval("historical_pick_value", "league", context, required=False, reason="Support future pick value context."),
    ]
    calculations = [_calc("pick_range_estimate", "Estimate likely pick range."), _calc("market_pick_value", "Estimate market value."), _calc("historical_pick_value", "Compare to historical pick values.")]
    validations = _common_validations() + [_validation("pick_identity", "Verify pick identity."), _validation("pick_ownership", "Verify current owner.")]
    return _plan(PlanType.DRAFT_PICK_EVALUATION, objective, DecisionEngine.DRAFT_PICK_EVALUATION, ResponseMode.STRUCTURED_EVALUATION, retrievals, validations, calculations=calculations, blockers=blockers, fallback=FallbackStrategy.LIMITED_WITHOUT_EXTERNAL_DATA.value, unnecessary=["prospect_recommendations"])


def _free_agent_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    retrievals = [
        _retrieval("free_agent_pool", "league", context, filters={"positions": interpreted.positions, "limit": interpreted.requested_count or 10}, reason="Load verified free-agent pool."),
        _retrieval("team_roster", "team", context, team_ids=[context.league_team_id], reason="Load roster fit."),
        _retrieval("cap_summary", "team", context, team_ids=[context.league_team_id], reason="Load affordability context."),
        _retrieval("league_acquisition_rules", "league", context, reason="Load waiver/free-agent rules."),
    ]
    calculations = [_calc("roster_fit", "Score roster fit."), _calc("expected_production", "Estimate production if available."), _calc("affordability", "Estimate affordability."), _calc("replacement_value", "Estimate replacement value."), _calc("risk_profile", "Assess player risk.")]
    validations = _common_validations() + [_validation("free_agent_availability", "Verify player is actually available."), _validation("rule_legality", "Verify acquisition legality."), _validation("cap_math", "Verify cap room."), _validation("roster_size_impact", "Verify roster room.")]
    return _plan(PlanType.FREE_AGENT, objective, DecisionEngine.FREE_AGENT, ResponseMode.RANKED_RECOMMENDATIONS, retrievals, validations, calculations=calculations, warnings=["trusted_free_agent_source_required"], fallback=FallbackStrategy.LIMITED_WITHOUT_EXTERNAL_DATA.value, unnecessary=["multi_year_draft_capital", "historical_trade_values"])


def _contract_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    blockers = _require_resolved_players(interpreted, minimum=1) if _needs_player_for_contract(interpreted) else []
    player_ids = _player_ids(interpreted)
    contract_filters = {}
    if interpreted.constraints.get("contract_years_left") is not None:
        contract_filters["contract_years_left"] = interpreted.constraints["contract_years_left"]
    if not player_ids:
        retrievals = [_retrieval("team_contracts", "team", context, team_ids=[context.league_team_id], filters=contract_filters, reason="Load active team contract facts.")]
    else:
        retrievals = [_retrieval("player_contract", "team", context, team_ids=[context.league_team_id], player_ids=player_ids, filters=contract_filters, reason="Load scoped player contract facts.")]
    subtype=interpreted.constraints.get("contract_query_type")
    if subtype in {"contract_roster_mismatch","contract_free_agent_status"}:
        retrievals.append(_retrieval("team_roster","team",context,team_ids=[context.league_team_id],player_ids=player_ids,reason="Keep roster authority separate from contract lifecycle."))
    if subtype=="contract_free_agent_status":
        retrievals.extend([_retrieval("free_agent_pool","league",context,player_ids=player_ids,required=False,reason="Verify publication separately from contract expiration."),_retrieval("league_acquisition_rules","league",context,required=False,reason="Do not infer signability from expiration." )])
    calculations = []
    mode = ResponseMode.DIRECT_FACTUAL
    engine = DecisionEngine.CONTRACT
    plan_type = PlanType.CONTRACT
    if objective.request_goal != Goal.FACTUAL_LOOKUP.value:
        retrievals.extend([
            _retrieval("player_profile", "league", context, player_ids=player_ids, reason="Load player age and value context."),
            _retrieval("cap_summary", "team", context, team_ids=[context.league_team_id], reason="Load future cap situation."),
        ])
        calculations = [_calc("extension_cost", "Estimate extension cost."), _calc("contract_efficiency", "Assess efficiency."), _calc("future_cap_impact", "Assess future cap impact."), _calc("replacement_cost", "Estimate replacement cost.")]
        mode = ResponseMode.STRUCTURED_EVALUATION
    validations = _common_validations() + [_validation("contract_scope", "Verify contract is scoped to active league."), _validation("data_freshness", "Check contract data freshness.", blocking=False)]
    return _plan(plan_type, objective, engine, mode, retrievals, validations, calculations=calculations, blockers=blockers, fallback=FallbackStrategy.FACTUAL_ONLY.value, unnecessary=["league_history", "all_rosters"])


def _salary_cap_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    strategic = objective.request_goal not in {Goal.FACTUAL_LOOKUP.value, Goal.UNCLEAR.value}
    retrievals = [_retrieval("cap_summary", "team", context, team_ids=[context.league_team_id], reason="Load canonical cap summary.")]
    calculations = [_calc("available_cap", "Return verified current available cap.")]
    mode = ResponseMode.DIRECT_FACTUAL
    if strategic:
        retrievals.append(_retrieval("team_contracts", "team", context, team_ids=[context.league_team_id], reason="Load contracts for cap planning."))
        calculations = calculations + [_calc("savings_by_action", "Identify savings by action."), _calc("dead_cap_impact", "Estimate dead-cap impact."), _calc("future_cap_impact", "Estimate future cap impact."), _calc("roster_value_loss", "Estimate roster value loss.")]
        mode = ResponseMode.STRUCTURED_EVALUATION
    validations = _common_validations() + [_validation("cap_math", "Validate cap values.")]
    return _plan(PlanType.SALARY_CAP, objective, DecisionEngine.SALARY_CAP, mode, retrievals, validations, calculations=calculations, fallback=FallbackStrategy.FACTUAL_ONLY.value, unnecessary=["all_rosters", "market_values", "trade_history", "projections"])


def _lineup_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    retrievals = [
        _retrieval("eligible_roster_players", "team", context, team_ids=[context.league_team_id], filters={"positions": interpreted.positions}, reason="Load eligible lineup players."),
        _retrieval("lineup_rules", "league", context, seasons=_seasons(context, interpreted), reason="Load lineup rules."),
        _retrieval("injury_status", "league", context, required=False, reason="Load injury status if available."),
        _retrieval("weekly_projection_summary", "league", context, required=False, reason="Load projections if available."),
    ]
    calculations = [_calc("lineup_projection", "Compare projected points."), _calc("floor_ceiling", "Compare floor and ceiling."), _calc("volatility", "Assess volatility.")]
    validations = _common_validations() + [_validation("week", "Verify current/requested week."), _validation("player_eligibility", "Verify lineup eligibility."), _validation("injury_freshness", "Check injury data freshness.", blocking=False), _validation("lineup_legality", "Validate lineup requirements.")]
    return _plan(PlanType.LINEUP, objective, DecisionEngine.LINEUP, ResponseMode.STRUCTURED_EVALUATION, retrievals, validations, calculations=calculations, fallback=FallbackStrategy.LIMITED_WITHOUT_EXTERNAL_DATA.value, unnecessary=["multi_year_draft_picks", "historical_trade_values", "long_term_contract_strategy"])


def _roster_move_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    player_ids = _player_ids(interpreted)
    raw = interpreted.raw_question.lower()
    retrievals = [
        _retrieval("team_roster", "team", context, team_ids=[context.league_team_id], player_ids=player_ids, reason="Load roster and move candidate."),
        _retrieval("player_contract", "team", context, team_ids=[context.league_team_id], player_ids=player_ids, reason="Load scoped contract/dead-cap context."),
        _retrieval("roster_rules", "league", context, reason="Load roster move rules."),
    ]
    calculations = [_calc("replacement_value", "Estimate replacement value.", required=False), _calc("roster_depth", "Assess depth impact.", required=False), _calc("dead_cap_impact", "Estimate dead-cap impact.")]
    if "cap" in raw or "save" in raw or "savings" in raw or "release" in raw or "drop" in raw or "cut" in raw:
        retrievals.append(_retrieval("cap_summary", "team", context, team_ids=[context.league_team_id], reason="Load current cap before the roster move."))
        calculations.extend([
            _calc("savings_by_action", "Calculate release savings."),
            _calc("post_transaction_cap", "Calculate post-release cap.", required=False),
        ])
    validations = _common_validations() + [_validation("player_ownership", "Verify player is on owner roster."), _validation("rule_legality", "Validate roster move legality."), _validation("roster_size_impact", "Validate roster-size impact.")]
    return _plan(PlanType.ROSTER_MOVE, objective, DecisionEngine.ROSTER_MOVE, ResponseMode.STRUCTURED_EVALUATION, retrievals, validations, calculations=calculations, blockers=_require_resolved_players(interpreted, minimum=1), fallback=FallbackStrategy.LIMITED_WITHOUT_EXTERNAL_DATA.value, unnecessary=["league_wide_rosters", "multi_year_draft_capital"])


def _long_term_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    retrievals = [
        _retrieval("team_roster", "team", context, team_ids=[context.league_team_id], reason="Load roster."),
        _retrieval("team_contracts", "team", context, team_ids=[context.league_team_id], reason="Load contracts."),
        _retrieval("cap_summary", "team", context, team_ids=[context.league_team_id], reason="Load cap."),
        _retrieval("draft_picks", "team", context, team_ids=[context.league_team_id], seasons=_seasons(context, interpreted), reason="Load draft capital."),
        _retrieval("team_brain", "team", context, team_ids=[context.league_team_id], reason="Load competitive window."),
    ]
    calculations = [_calc("year_by_year_roster_outlook", "Plan future roster outlook."), _calc("cap_flexibility", "Assess future cap flexibility."), _calc("contract_exposure", "Assess contract exposure."), _calc("age_concentration", "Assess age concentration."), _calc("future_asset_strength", "Assess future assets."), _calc("positional_replacement_priority", "Identify replacement priorities.")]
    return _plan(PlanType.LONG_TERM_PLANNING, objective, DecisionEngine.LONG_TERM_PLANNING, ResponseMode.STRUCTURED_EVALUATION, retrievals, _common_validations(), calculations=calculations, fallback=FallbackStrategy.INTERNAL_DATA_ONLY.value, unnecessary=["weekly_lineup_projections_by_default", "unbounded_transaction_history"])


def _team_comparison_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    team_ids = _team_ids(interpreted, context)
    blockers = []
    if len(set(team_ids + [context.league_team_id])) < 2:
        blockers.append(_blocker("unresolved_fantasy_team", "Team comparison requires another resolved active-league team.", "fantasy_team_refs"))
    retrievals = [_retrieval("team_brain", "league", context, team_ids=_bounded_team_ids([context.league_team_id] + team_ids), reason="Load compared team brain rows."), _retrieval("team_roster_summary", "league", context, team_ids=_bounded_team_ids([context.league_team_id] + team_ids), reason="Load compact roster summaries.")]
    calculations = [_calc("team_strength_comparison", "Compare team strength."), _calc("positional_need_comparison", "Compare needs."), _calc("competitive_window_fit", "Compare windows.")]
    validations = _common_validations() + [_validation("team_scope", "Verify teams are in active league.")]
    return _plan(PlanType.TEAM_COMPARISON, objective, DecisionEngine.TEAM_COMPARISON, ResponseMode.SCENARIO_COMPARISON, retrievals, validations, calculations=calculations, blockers=blockers, fallback=FallbackStrategy.CLARIFICATION_REQUIRED.value, unnecessary=["another_user_memory"])


def _league_analysis_plan(context: AssistantRequestContext, _state: ConversationState | None, interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    retrievals = [_retrieval("league_brain", "league", context, reason="Load league summary."), _retrieval("team_brain_rankings", "league_wide", context, filters={"limit": MAX_TARGET_COUNT}, reason="Load bounded team rankings.")]
    calculations = [_calc("league_strength_summary", "Summarize league/team strength."), _calc("trade_fit_context", "Identify high-level trade-fit context.")]
    return _plan(PlanType.LEAGUE_ANALYSIS, objective, DecisionEngine.LEAGUE_ANALYSIS, ResponseMode.STRUCTURED_EVALUATION, retrievals, _common_validations(), calculations=calculations, fallback=FallbackStrategy.INTERNAL_DATA_ONLY.value, unnecessary=["personal_owner_memory_for_other_users", "all_transactions_without_limit"])


def _general_conversation_plan(context: AssistantRequestContext, _state: ConversationState | None, _interpreted: InterpretedQuestion, objective: OwnerObjective) -> DecisionPlan:
    return _plan(PlanType.GENERAL_CONVERSATION, objective, DecisionEngine.CONVERSATION, ResponseMode.GENERAL_CONVERSATION, [], _common_validations(), fallback=FallbackStrategy.NONE.value, unnecessary=["league_data", "tools", "calculations"])


def _unsupported_plan(context: AssistantRequestContext, interpreted: InterpretedQuestion, objective: OwnerObjective, blockers: list[PlanBlocker]) -> DecisionPlan:
    blockers = blockers + [_blocker("unsupported_intent", "This request is outside supported fantasy GM planning scope.", interpreted.primary_intent, resolvable=False)]
    return _plan(PlanType.UNSUPPORTED, objective, DecisionEngine.UNSUPPORTED, ResponseMode.UNSUPPORTED, [], _common_validations(), blockers=blockers, fallback=FallbackStrategy.UNSUPPORTED.value, unnecessary=["all_league_data", "calculations", "retrieval"])


def _blocked_plan(context: AssistantRequestContext, interpreted: InterpretedQuestion, objective: OwnerObjective, blockers: list[PlanBlocker]) -> DecisionPlan:
    fallback = FallbackStrategy.CLARIFICATION_REQUIRED.value
    return _plan(PlanType.BLOCKED, objective, DecisionEngine.CONVERSATION, ResponseMode.CLARIFICATION_REQUIRED, [], _common_validations(), blockers=blockers, fallback=fallback, unnecessary=["broad_retrieval_until_clarified"])


def _plan(
    plan_type: PlanType,
    objective: OwnerObjective,
    engine: DecisionEngine,
    response_mode: ResponseMode,
    retrievals: list[RetrievalRequest],
    validations: list[ValidationStep],
    *,
    rules: list[RuleRequest] | None = None,
    calculations: list[CalculationRequest] | None = None,
    blockers: list[PlanBlocker] | None = None,
    warnings: list[str] | None = None,
    fallback: str | None = FallbackStrategy.NONE.value,
    unnecessary: list[str] | None = None,
) -> DecisionPlan:
    return DecisionPlan(
        plan_type=plan_type.value,
        request_goal=objective.request_goal,
        active_strategic_goal=objective.active_strategic_goal,
        decision_engine=engine.value,
        response_mode=response_mode.value,
        retrieval_requests=retrievals,
        rule_requests=rules or [],
        calculation_requests=calculations or [],
        validation_steps=validations,
        required_entities=[],
        required_inputs=[],
        optional_inputs=[],
        blockers=blockers or [],
        warnings=warnings or [],
        unnecessary_data=unnecessary or [],
        fallback_strategy=fallback,
        ready_for_execution=True,
        confidence=objective.confidence,
    )


PLANNER_HANDLERS: dict[str, PlanHandler] = {
    Intent.DATA_LOOKUP.value: _factual_lookup_plan,
    Intent.FOLLOW_UP.value: _factual_lookup_plan,
    Intent.RULES_QUESTION.value: _rules_plan,
    Intent.PLAYER_EVALUATION.value: _player_evaluation_plan,
    Intent.PLAYER_COMPARISON.value: _player_comparison_plan,
    Intent.ROSTER_EVALUATION.value: _roster_evaluation_plan,
    Intent.TRADE_EVALUATION.value: _trade_evaluation_plan,
    Intent.TRADE_DISCOVERY.value: _trade_discovery_plan,
    Intent.TRADE_CONSTRUCTION.value: _trade_construction_plan,
    Intent.DRAFT_RECOMMENDATION.value: _draft_recommendation_plan,
    Intent.DRAFT_PICK_EVALUATION.value: _draft_pick_evaluation_plan,
    Intent.FREE_AGENT_RECOMMENDATION.value: _free_agent_plan,
    Intent.CONTRACT_QUESTION.value: _contract_plan,
    Intent.SALARY_CAP_QUESTION.value: _salary_cap_plan,
    Intent.LINEUP_QUESTION.value: _lineup_plan,
    Intent.ROSTER_MOVE_QUESTION.value: _roster_move_plan,
    Intent.LONG_TERM_PLANNING.value: _long_term_plan,
    Intent.TEAM_COMPARISON.value: _team_comparison_plan,
    Intent.LEAGUE_ANALYSIS.value: _league_analysis_plan,
    Intent.SCENARIO_SIMULATION.value: _scenario_simulation_plan,
    Intent.GENERAL_CONVERSATION.value: _general_conversation_plan,
}


def _finalize_plan(plan: DecisionPlan) -> DecisionPlan:
    retrievals, retrieval_warnings = _dedupe_retrievals(plan.retrieval_requests)
    rules = _dedupe_by_key(plan.rule_requests, lambda item: (item.rule_type, item.season, tuple(item.related_entity_ids)))
    calculations = _dedupe_by_key(plan.calculation_requests, lambda item: (item.calculation_type, item.output_key))
    validations = _dedupe_by_key(plan.validation_steps, lambda item: (item.validation_type, item.reason))
    blockers = _dedupe_by_key(plan.blockers, lambda item: (item.blocker_type, item.explanation, item.source_ref))
    league_wide_count = sum(1 for item in retrievals if item.scope == "league_wide")
    warnings = list(dict.fromkeys((plan.warnings or []) + retrieval_warnings))
    if league_wide_count > MAX_LEAGUE_WIDE_RETRIEVALS:
        blockers.append(_blocker("scope_limit_exceeded", "Plan requests too many league-wide retrievals.", "retrieval_requests"))
    ready = plan.ready_for_execution and not blockers and plan.plan_type not in {PlanType.UNSUPPORTED.value, PlanType.BLOCKED.value}
    response_mode = ResponseMode.CLARIFICATION_REQUIRED.value if blockers and plan.response_mode != ResponseMode.UNSUPPORTED.value else plan.response_mode
    fallback = FallbackStrategy.CLARIFICATION_REQUIRED.value if blockers and plan.fallback_strategy != FallbackStrategy.UNSUPPORTED.value else plan.fallback_strategy
    confidence = "low" if blockers else plan.confidence
    return DecisionPlan(
        plan_type=PlanType.BLOCKED.value if blockers and plan.plan_type != PlanType.UNSUPPORTED.value else plan.plan_type,
        request_goal=plan.request_goal,
        active_strategic_goal=plan.active_strategic_goal,
        decision_engine=plan.decision_engine,
        response_mode=response_mode,
        retrieval_requests=retrievals,
        rule_requests=rules,
        calculation_requests=calculations,
        validation_steps=validations,
        required_entities=_dedupe_entities(plan.required_entities + _entities_from_plan(retrievals)),
        required_inputs=_dedupe_strings(plan.required_inputs + [r.retrieval_type for r in retrievals if r.required] + [r.rule_type for r in rules if r.required] + [c.calculation_type for c in calculations if c.required]),
        optional_inputs=_dedupe_strings(plan.optional_inputs + [r.retrieval_type for r in retrievals if not r.required] + [c.calculation_type for c in calculations if not c.required]),
        blockers=blockers,
        warnings=warnings,
        unnecessary_data=_dedupe_strings(plan.unnecessary_data),
        fallback_strategy=fallback,
        ready_for_execution=ready,
        confidence=confidence,
    )


def _base_blockers(context: AssistantRequestContext, interpreted: InterpretedQuestion, objective: OwnerObjective) -> list[PlanBlocker]:
    blockers = []
    if not context.league_id or not context.league_team_id:
        blockers.append(_blocker("missing_canonical_team", "Planner requires authenticated league and team scope.", "AssistantRequestContext"))
    for ambiguity in interpreted.ambiguities:
        if getattr(ambiguity, "blocking", False):
            blockers.append(_blocker(ambiguity.ambiguity_type, ambiguity.explanation or "Blocking interpretation ambiguity.", "InterpretedQuestion"))
    for ambiguity in objective.ambiguities:
        if getattr(ambiguity, "blocking", False):
            if ambiguity.ambiguity_type == "unclear_objective":
                continue
            blockers.append(_blocker(ambiguity.ambiguity_type, ambiguity.explanation or "Blocking objective ambiguity.", "OwnerObjective"))
    if _has_conflicting_hard_constraints(objective):
        blockers.append(_blocker("conflicting_hard_constraints", "Hard objective constraints conflict.", "OwnerObjective.non_negotiables"))
    return blockers


def _has_conflicting_hard_constraints(objective: OwnerObjective) -> bool:
    types = {constraint.constraint_type for constraint in objective.non_negotiables if constraint.hard}
    return "do_not_trade_first_round_pick" in types and "allow_trade_first_round_pick" in types


def _require_resolved_players(interpreted: InterpretedQuestion, *, minimum: int) -> list[PlanBlocker]:
    resolved = _player_ids(interpreted)
    if len(resolved) >= minimum:
        return []
    return [_blocker("unresolved_player", f"Plan requires at least {minimum} resolved player reference(s).", "player_refs")]


def _require_resolved_trade_assets(interpreted: InterpretedQuestion) -> list[PlanBlocker]:
    blockers = []
    if any(asset.asset_type == "player" and not asset.canonical_id for asset in interpreted.included_assets):
        blockers.append(_blocker("unresolved_player", "Included player asset is unresolved.", "included_assets"))
    return blockers


def _retrieval(
    retrieval_type: str,
    scope: str,
    context: AssistantRequestContext,
    *,
    entity_ids: list[str] | None = None,
    team_ids: list[str] | None = None,
    player_ids: list[str] | None = None,
    pick_ids: list[str] | None = None,
    seasons: list[int] | None = None,
    filters: dict[str, Any] | None = None,
    required: bool = True,
    reason: str = "",
    freshness: str | None = "current",
) -> RetrievalRequest:
    base_filters = {"league_id": context.league_id}
    if scope == "team":
        base_filters["league_team_id"] = context.league_team_id
    for key, value in (filters or {}).items():
        if value not in (None, [], ""):
            base_filters[key] = value
    return RetrievalRequest(
        retrieval_type=retrieval_type,
        scope=scope,
        entity_ids=_dedupe_strings(entity_ids or []),
        team_ids=_dedupe_strings(team_ids or ([] if scope != "team" else [context.league_team_id])),
        player_ids=_dedupe_strings(player_ids or []),
        pick_ids=_dedupe_strings(pick_ids or []),
        seasons=_bounded_seasons(seasons or [context.requested_season]),
        filters=base_filters,
        required=required,
        reason=reason,
        freshness_requirement=freshness,
    )


def _rule(rule_type: str, season: int | None, reason: str, *, required: bool = True, related_entity_ids: list[str] | None = None) -> RuleRequest:
    return RuleRequest(rule_type, season, required, reason, _dedupe_strings(related_entity_ids or []))


def _calc(calculation_type: str, reason: str, *, required: bool = True, output_key: str | None = None, input_refs: list[str] | None = None) -> CalculationRequest:
    return CalculationRequest(calculation_type, required, reason, _dedupe_strings(input_refs or []), output_key or calculation_type)


def _validation(validation_type: str, reason: str, *, required: bool = True, blocking: bool = True) -> ValidationStep:
    return ValidationStep(validation_type, required, reason, blocking)


def _blocker(blocker_type: str, explanation: str, source_ref: str | None, *, resolvable: bool = True) -> PlanBlocker:
    return PlanBlocker(blocker_type, explanation, source_ref, resolvable)


def _common_validations() -> list[ValidationStep]:
    return [
        _validation("context_scope", "Verify authenticated league and team scope."),
        _validation("league_membership", "Verify authenticated user belongs to the requested league."),
        _validation("season_consistency", "Verify requested season is consistent with context.", blocking=False),
    ]


def _player_ids(interpreted: InterpretedQuestion) -> list[str]:
    return _dedupe_strings([
        ref.canonical_id
        for ref in interpreted.player_refs
        if ref.canonical_id and ref.resolution_status in {ResolutionStatus.RESOLVED.value, ResolutionStatus.INFERRED_FROM_CONVERSATION.value}
    ])


def _team_ids(interpreted: InterpretedQuestion, context: AssistantRequestContext) -> list[str]:
    return _dedupe_strings([
        ref.canonical_id
        for ref in interpreted.fantasy_team_refs
        if ref.canonical_id and ref.canonical_id != context.league_team_id and ref.resolution_status == ResolutionStatus.RESOLVED.value
    ])


def _pick_labels(interpreted: InterpretedQuestion) -> list[str]:
    labels = []
    for pick in interpreted.pick_refs:
        if pick.canonical_pick_id:
            labels.append(pick.canonical_pick_id)
        elif pick.slot and pick.round:
            labels.append(f"{pick.round}.{pick.slot:02d}")
        elif pick.round:
            labels.append(f"{pick.season or 'future'}_round_{pick.round}")
    return _dedupe_strings(labels)


def _seasons(context: AssistantRequestContext, interpreted: InterpretedQuestion) -> list[int]:
    seasons = list(interpreted.seasons or [])
    if not seasons:
        seasons = [context.requested_season]
    return _bounded_seasons(seasons)


def _bounded_seasons(seasons: list[int]) -> list[int]:
    out = []
    for season in seasons:
        try:
            value = int(season)
        except Exception:
            continue
        if value not in out:
            out.append(value)
    return out[:MAX_DEFAULT_SEASONS]


def _bounded_team_ids(team_ids: list[str]) -> list[str]:
    return _dedupe_strings(team_ids)[:4]


def _has_any(normalized: str, markers: tuple[str, ...]) -> bool:
    return any(marker in normalized for marker in markers)


def _needs_player_for_contract(interpreted: InterpretedQuestion) -> bool:
    raw = interpreted.raw_question.lower()
    return any(term in raw for term in ("his contract", "her contract", "this player", "extend", "contract for", "what is"))


def _dedupe_retrievals(items: list[RetrievalRequest]) -> tuple[list[RetrievalRequest], list[str]]:
    out = []
    seen = set()
    warnings = []
    for item in items:
        key = (
            item.retrieval_type,
            item.scope,
            tuple(item.team_ids),
            tuple(item.player_ids),
            tuple(item.pick_ids),
            tuple(item.seasons),
            tuple(sorted((str(k), str(v)) for k, v in item.filters.items())),
        )
        if key in seen:
            continue
        if len(out) >= MAX_RETRIEVAL_REQUESTS:
            warnings.append("retrieval_request_limit_applied")
            continue
        out.append(item)
        seen.add(key)
    return out, warnings


def _dedupe_by_key(items: list[Any], key_fn: Callable[[Any], tuple]) -> list[Any]:
    out = []
    seen = set()
    for item in items:
        key = key_fn(item)
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _dedupe_strings(items: list[Any]) -> list[str]:
    out = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _entities_from_plan(retrievals: list[RetrievalRequest]) -> list[PlanEntity]:
    entities: list[PlanEntity] = []
    for retrieval in retrievals:
        for player_id in retrieval.player_ids:
            entities.append(PlanEntity("player", player_id, None, retrieval.required, True))
        for team_id in retrieval.team_ids:
            entities.append(PlanEntity("fantasy_team", team_id, None, retrieval.required, True))
        for pick_id in retrieval.pick_ids:
            entities.append(PlanEntity("draft_pick", pick_id, None, retrieval.required, True))
    return entities


def _dedupe_entities(items: list[PlanEntity]) -> list[PlanEntity]:
    return _dedupe_by_key(items, lambda item: (item.entity_type, item.canonical_id, item.raw_text, item.required, item.resolved))
