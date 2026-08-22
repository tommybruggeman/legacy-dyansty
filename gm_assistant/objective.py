from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from gm_assistant.conversation_state import ConversationState, ConversationStateUpdate
from gm_assistant.interpretation import InterpretedQuestion, Intent, ResolutionStatus
from gm_assistant.request_context import AssistantRequestContext


OBJECTIVE_VERSION = "gm_owner_objective.v1"


class Goal(str, Enum):
    FACTUAL_LOOKUP = "factual_lookup"
    EVALUATE_ASSET = "evaluate_asset"
    COMPARE_ASSETS = "compare_assets"
    IMPROVE_CURRENT_ROSTER = "improve_current_roster"
    WIN_NOW = "win_now"
    CONTEND_THIS_SEASON = "contend_this_season"
    REBUILD = "rebuild"
    RETOOL = "retool"
    GET_YOUNGER = "get_younger"
    INCREASE_FUTURE_VALUE = "increase_future_value"
    PRESERVE_DRAFT_CAPITAL = "preserve_draft_capital"
    ACQUIRE_DRAFT_CAPITAL = "acquire_draft_capital"
    IMPROVE_SPECIFIC_POSITION = "improve_specific_position"
    REDUCE_SALARY = "reduce_salary"
    PRESERVE_CAP_FLEXIBILITY = "preserve_cap_flexibility"
    IMPROVE_CONTRACT_EFFICIENCY = "improve_contract_efficiency"
    REDUCE_RISK = "reduce_risk"
    INCREASE_UPSIDE = "increase_upside"
    IMPROVE_DEPTH = "improve_depth"
    REPLACE_STARTER = "replace_starter"
    PREPARE_FOR_FUTURE_SEASON = "prepare_for_future_season"
    EVALUATE_LEGALITY = "evaluate_legality"
    UNDERSTAND_RULES = "understand_rules"
    CONSTRUCT_TRANSACTION = "construct_transaction"
    DISCOVER_TARGETS = "discover_targets"
    OPTIMIZE_LINEUP = "optimize_lineup"
    LONG_TERM_ROSTER_PLAN = "long_term_roster_plan"
    UNCLEAR = "unclear"


class RiskTolerance(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceType(str, Enum):
    EXPLICIT_CURRENT_MESSAGE = "explicit_current_message"
    ACTIVE_CONVERSATION_STATE = "active_conversation_state"
    DURABLE_OWNER_PREFERENCE = "durable_owner_preference"
    INTERPRETED_CONSTRAINT = "interpreted_constraint"
    TEAM_BRAIN = "team_brain"
    FACTUAL_TEAM_CONTEXT = "factual_team_context"
    INTENT_DEFAULT = "intent_default"
    DEFAULT = "default"


@dataclass(frozen=True)
class ObjectiveConstraint:
    constraint_type: str
    value: Any
    source: str
    hard: bool
    scope: str


@dataclass(frozen=True)
class ObjectiveSource:
    source_type: str
    source_ref: str | None
    statement: str
    priority: int


@dataclass(frozen=True)
class ObjectiveTeamContext:
    team_direction: str | None = None
    competitive_window: str | None = None
    roster_strength_summary: dict[str, Any] = field(default_factory=dict)
    positional_need_summary: dict[str, Any] = field(default_factory=dict)
    cap_summary: dict[str, Any] = field(default_factory=dict)
    contract_timeline_summary: dict[str, Any] = field(default_factory=dict)
    draft_capital_summary: dict[str, Any] = field(default_factory=dict)
    data_quality_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StrategicConflict:
    conflict_type: str
    stated_goal: str
    factual_signal: str
    severity: str
    explanation: str


@dataclass(frozen=True)
class ObjectiveAmbiguity:
    ambiguity_type: str
    explanation: str
    blocking: bool
    candidates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OwnerObjective:
    request_goal: str
    active_strategic_goal: str | None
    primary_goal: str
    secondary_goals: list[str] = field(default_factory=list)
    timeframe: str | None = None
    target_seasons: list[int] = field(default_factory=list)
    horizon_years: int | None = None
    risk_tolerance: str = RiskTolerance.UNKNOWN.value
    priority_weights: dict[str, str] = field(default_factory=dict)
    non_negotiables: list[ObjectiveConstraint] = field(default_factory=list)
    acceptable_tradeoffs: list[str] = field(default_factory=list)
    soft_preferences: list[ObjectiveConstraint] = field(default_factory=list)
    factual_context: ObjectiveTeamContext = field(default_factory=ObjectiveTeamContext)
    source_evidence: list[ObjectiveSource] = field(default_factory=list)
    explicitness: str = "inferred"
    confidence: str = Confidence.MEDIUM.value
    strategic_conflicts: list[StrategicConflict] = field(default_factory=list)
    ambiguities: list[ObjectiveAmbiguity] = field(default_factory=list)
    objective_version: str = OBJECTIVE_VERSION

    def to_packet(self) -> dict[str, Any]:
        return asdict(self)


def build_owner_objective(
    *,
    context: AssistantRequestContext,
    conversation_state: ConversationState | None,
    interpreted_question: InterpretedQuestion,
    owner_preferences: dict[str, Any] | None = None,
    team_context: dict[str, Any] | None = None,
) -> OwnerObjective:
    raw = interpreted_question.raw_question or ""
    normalized = _normalize(raw)
    sources: list[ObjectiveSource] = []
    ambiguities: list[ObjectiveAmbiguity] = []
    team_summary = build_objective_team_context(team_context or {}, owner_preferences or {})
    explicit_goals = _detect_explicit_goals(normalized, interpreted_question)
    source_goal = explicit_goals[0] if explicit_goals else None
    secondary_goals = explicit_goals[1:]
    active_goal = _normalize_goal((conversation_state.active_objective if conversation_state else None))
    intent_goal = _intent_default(interpreted_question.primary_intent)

    if source_goal:
        request_goal = source_goal
        active_strategic_goal = None if _is_factual_goal(source_goal) else source_goal
        sources.append(_source(SourceType.EXPLICIT_CURRENT_MESSAGE, raw[:180], 1))
    elif _is_factual_goal(intent_goal):
        request_goal = intent_goal
        active_strategic_goal = active_goal
        sources.append(_source(SourceType.INTENT_DEFAULT, interpreted_question.primary_intent, 2))
    elif active_goal:
        request_goal = _intent_default(interpreted_question.primary_intent)
        active_strategic_goal = active_goal
        sources.append(_source(SourceType.ACTIVE_CONVERSATION_STATE, active_goal, 3))
        if request_goal == Goal.UNCLEAR.value:
            request_goal = active_goal
    else:
        durable_goal = _goal_from_preferences(owner_preferences or {})
        if durable_goal:
            request_goal = durable_goal
            active_strategic_goal = durable_goal
            sources.append(_source(SourceType.DURABLE_OWNER_PREFERENCE, durable_goal, 5))
        else:
            request_goal = _intent_default(interpreted_question.primary_intent)
            active_strategic_goal = None if _is_factual_goal(request_goal) else None
            sources.append(_source(SourceType.INTENT_DEFAULT, interpreted_question.primary_intent, 7))

    constraints, soft_preferences = _constraints_from_interpretation(interpreted_question, normalized)
    if constraints or soft_preferences:
        sources.append(_source(SourceType.INTERPRETED_CONSTRAINT, "structured_constraints", 4))
    durable_constraints, durable_soft_preferences = _constraints_from_preferences(owner_preferences or {}, normalized)
    constraints = _dedupe_constraints(constraints + durable_constraints)
    soft_preferences = _dedupe_constraints(soft_preferences + durable_soft_preferences)

    constraints, soft_preferences = _apply_current_overrides(normalized, constraints, soft_preferences)
    secondary_goals = _dedupe_goals(secondary_goals + _goals_from_constraints(constraints, soft_preferences, interpreted_question))
    if request_goal in secondary_goals:
        secondary_goals = [goal for goal in secondary_goals if goal != request_goal]

    timeframe, target_seasons, horizon_years = _resolve_timeframe(
        interpreted_question,
        context,
        conversation_state,
        normalized,
    )
    risk_tolerance = _resolve_risk(normalized, owner_preferences or {})
    priority_weights = _priority_weights(request_goal, secondary_goals, constraints, soft_preferences, interpreted_question, risk_tolerance)
    acceptable_tradeoffs = _acceptable_tradeoffs(normalized, owner_preferences or {}, request_goal, risk_tolerance)
    ambiguities.extend(_objective_ambiguities(request_goal, interpreted_question, normalized, active_goal))
    conflicts = _strategic_conflicts(
        request_goal=request_goal,
        secondary_goals=secondary_goals,
        constraints=constraints,
        interpreted_question=interpreted_question,
        team_context=team_summary,
        normalized=normalized,
    )
    confidence = _objective_confidence(request_goal, interpreted_question, ambiguities, team_summary)
    explicitness = "explicit" if source_goal else "conversation" if active_goal else "durable" if sources and sources[-1].source_type == SourceType.DURABLE_OWNER_PREFERENCE.value else "inferred"

    if not team_summary.team_direction:
        sources.append(_source(SourceType.FACTUAL_TEAM_CONTEXT, "team_context_missing", 6))

    return OwnerObjective(
        request_goal=request_goal,
        active_strategic_goal=active_strategic_goal,
        primary_goal=request_goal,
        secondary_goals=secondary_goals,
        timeframe=timeframe,
        target_seasons=target_seasons,
        horizon_years=horizon_years,
        risk_tolerance=risk_tolerance,
        priority_weights=priority_weights,
        non_negotiables=constraints,
        acceptable_tradeoffs=acceptable_tradeoffs,
        soft_preferences=soft_preferences,
        factual_context=team_summary,
        source_evidence=sources,
        explicitness=explicitness,
        confidence=confidence,
        strategic_conflicts=conflicts,
        ambiguities=ambiguities,
    )


def conversation_update_from_objective(
    objective: OwnerObjective,
    *,
    message_id: str | None = None,
) -> ConversationStateUpdate:
    update = ConversationStateUpdate(last_message_id=message_id)
    if objective.request_goal == Goal.FACTUAL_LOOKUP.value or objective.request_goal == Goal.UNDERSTAND_RULES.value:
        pass
    elif objective.active_strategic_goal:
        update.replace_objective = objective.active_strategic_goal
    if objective.timeframe and objective.request_goal not in {Goal.FACTUAL_LOOKUP.value, Goal.UNDERSTAND_RULES.value}:
        update.replace_timeframe = objective.timeframe
    update.add_constraints = {
        constraint.constraint_type: constraint.value
        for constraint in objective.non_negotiables
        if constraint.scope == "current_request"
    }
    update.add_ambiguities = [
        ambiguity.ambiguity_type
        for ambiguity in objective.ambiguities
        if ambiguity.blocking
    ]
    for constraint in objective.non_negotiables + objective.soft_preferences:
        if constraint.constraint_type == "allow_trade_first_round_pick":
            update.remove_constraint_keys.append("do_not_trade_first_round_pick")
    return update


def build_objective_packet(objective: OwnerObjective | None) -> dict[str, Any]:
    if not objective:
        return {}
    packet = objective.to_packet()
    factual = packet.get("factual_context") or {}
    packet["factual_context"] = {
        "team_direction": factual.get("team_direction"),
        "competitive_window": factual.get("competitive_window"),
        "roster_strength_summary": factual.get("roster_strength_summary"),
        "positional_need_summary": factual.get("positional_need_summary"),
        "cap_summary": factual.get("cap_summary"),
        "contract_timeline_summary": factual.get("contract_timeline_summary"),
        "draft_capital_summary": factual.get("draft_capital_summary"),
        "data_quality_warnings": factual.get("data_quality_warnings"),
    }
    return packet


def build_objective_team_context(team_context: dict[str, Any], owner_preferences: dict[str, Any] | None = None) -> ObjectiveTeamContext:
    team_brain = dict((team_context or {}).get("team_brain") or {})
    cap = _compact_cap((team_context or {}).get("cap_summary") or (team_context or {}).get("cap") or {})
    draft = _compact_draft((team_context or {}).get("draft_picks") or (team_context or {}).get("draft_capital") or {})
    warnings = []
    if not team_brain:
        warnings.append("team_brain_missing")
    if (owner_preferences or {}).get("memory_load_error"):
        warnings.append("durable_memory_retrieval_failed")
    direction = _clean_text(team_brain.get("team_direction"))
    return ObjectiveTeamContext(
        team_direction=direction,
        competitive_window=_competitive_window(team_brain),
        roster_strength_summary={
            "strengths": _compact_list(team_brain.get("position_strengths"), 8),
            "core_count": len(team_brain.get("core_players") or []),
        },
        positional_need_summary={
            "needs": _compact_list(team_brain.get("position_needs"), 8),
            "contract_problems_count": len(team_brain.get("contract_problems") or []),
        },
        cap_summary=cap,
        contract_timeline_summary={
            "contract_problems_count": len(team_brain.get("contract_problems") or []),
        },
        draft_capital_summary=draft,
        data_quality_warnings=warnings,
    )


def _detect_explicit_goals(normalized: str, interpreted: InterpretedQuestion) -> list[str]:
    goals: list[str] = []
    if _has_any(normalized, ("build me a trade", "build a trade", "what should i offer", "trade package", "package for")):
        goals.append(Goal.CONSTRUCT_TRANSACTION.value)
    if _has_any(normalized, ("older veteran", "veteran receiver", "veteran running back", "veteran quarterback", "veteran tight end")):
        goals.append(Goal.IMPROVE_CURRENT_ROSTER.value)
    if _has_any(normalized, ("forget rebuilding", "forget the rebuild", "not rebuilding")):
        goals.append(Goal.WIN_NOW.value if _has_any(normalized, ("win", "contend", "compete")) else Goal.RETOOL.value)
    if _has_any(normalized, ("win this year", "win now", "all in", "going all in", "immediate production")):
        goals.append(Goal.WIN_NOW.value)
    if _has_any(normalized, ("contend this season", "compete this season", "help me contend", "compete this year")):
        goals.append(Goal.CONTEND_THIS_SEASON.value)
    if _has_any(normalized, ("i am rebuilding", "im rebuilding", "want to rebuild", "tear it down", "planning for the future", "move veterans for picks", "care more about 2028")):
        goals.append(Goal.REBUILD.value)
    if _has_any(normalized, ("stay competitive but get younger", "reset without bottoming out", "not a full rebuild", "do not want a full rebuild")):
        goals.append(Goal.RETOOL.value)
    if _has_any(normalized, ("get younger", "younger players", "young receivers", "young running backs")):
        goals.append(Goal.GET_YOUNGER.value)
    if _has_any(normalized, ("want more picks", "acquire picks", "move veterans for future picks")):
        goals.append(Goal.ACQUIRE_DRAFT_CAPITAL.value)
    if _has_any(normalized, ("do not trade my first", "dont trade my first", "without moving my first", "preserve my first")):
        goals.append(Goal.PRESERVE_DRAFT_CAPITAL.value)
    if _has_any(normalized, ("clear salary", "reduce salary", "cheaper players", "cut salary")):
        goals.append(Goal.REDUCE_SALARY.value)
    if _has_any(normalized, ("preserve future cap", "preserve cap", "cap flexibility", "keep cap flexibility")):
        goals.append(Goal.PRESERVE_CAP_FLEXIBILITY.value)
    if interpreted.positions and _has_any(normalized, ("need a", "need more", "improve my", "upgrade", "replace my starting")):
        goals.append(Goal.IMPROVE_SPECIFIC_POSITION.value)
    if _has_any(normalized, ("tight-end", "tight end", "receiver", "running back", "quarterback", "rb", "wr", "qb", "te")) and _has_any(normalized, ("need more", "need a", "improve", "upgrade", "replace")):
        goals.append(Goal.IMPROVE_SPECIFIC_POSITION.value)
    if _has_any(normalized, ("more depth", "improve depth", "depth at")):
        goals.append(Goal.IMPROVE_DEPTH.value)
    if _has_any(normalized, ("replace my starting", "replace starter", "new starter")):
        goals.append(Goal.REPLACE_STARTER.value)
    if _has_any(normalized, ("prioritize upside", "take a swing", "willing to take a swing", "ceiling")):
        goals.append(Goal.INCREASE_UPSIDE.value)
    if _has_any(normalized, ("safer option", "safe option", "reduce risk", "avoid injury risk", "proven production")):
        goals.append(Goal.REDUCE_RISK.value)
    return _dedupe_goals(goals)


def _intent_default(intent: str) -> str:
    mapping = {
        Intent.DATA_LOOKUP.value: Goal.FACTUAL_LOOKUP.value,
        Intent.RULES_QUESTION.value: Goal.UNDERSTAND_RULES.value,
        Intent.CONTRACT_QUESTION.value: Goal.FACTUAL_LOOKUP.value,
        Intent.SALARY_CAP_QUESTION.value: Goal.FACTUAL_LOOKUP.value,
        Intent.PLAYER_EVALUATION.value: Goal.EVALUATE_ASSET.value,
        Intent.PLAYER_COMPARISON.value: Goal.COMPARE_ASSETS.value,
        Intent.TRADE_EVALUATION.value: Goal.EVALUATE_ASSET.value,
        Intent.TRADE_DISCOVERY.value: Goal.DISCOVER_TARGETS.value,
        Intent.TRADE_CONSTRUCTION.value: Goal.CONSTRUCT_TRANSACTION.value,
        Intent.DRAFT_RECOMMENDATION.value: Goal.IMPROVE_CURRENT_ROSTER.value,
        Intent.LINEUP_QUESTION.value: Goal.OPTIMIZE_LINEUP.value,
        Intent.LONG_TERM_PLANNING.value: Goal.LONG_TERM_ROSTER_PLAN.value,
        Intent.ROSTER_EVALUATION.value: Goal.IMPROVE_CURRENT_ROSTER.value,
    }
    return mapping.get(intent, Goal.UNCLEAR.value)


def _constraints_from_interpretation(interpreted: InterpretedQuestion, normalized: str) -> tuple[list[ObjectiveConstraint], list[ObjectiveConstraint]]:
    hard: list[ObjectiveConstraint] = []
    soft: list[ObjectiveConstraint] = []
    for key, value in (interpreted.constraints or {}).items():
        hard_constraint = key in {
            "do_not_trade_first_round_pick",
            "max_age",
            "max_salary",
            "max_contract_years",
            "only_free_agents",
        }
        if key in {"prefers_younger_players", "prefers_cheap_assets", "preserve_cap_flexibility"} and _soft_wording(normalized):
            hard_constraint = False
        constraint = ObjectiveConstraint(key, value, SourceType.INTERPRETED_CONSTRAINT.value, hard_constraint, "current_request")
        if hard_constraint:
            hard.append(constraint)
        else:
            soft.append(constraint)
    for asset in interpreted.excluded_assets:
        constraint = ObjectiveConstraint("exclude_asset", asdict(asset), SourceType.INTERPRETED_CONSTRAINT.value, True, "current_request")
        hard.append(constraint)
    for position in interpreted.positions:
        soft.append(ObjectiveConstraint("required_position", position, SourceType.INTERPRETED_CONSTRAINT.value, False, "current_request"))
    return hard, soft


def _constraints_from_preferences(owner_preferences: dict[str, Any], normalized: str) -> tuple[list[ObjectiveConstraint], list[ObjectiveConstraint]]:
    if _has_any(normalized, ("older veteran", "veteran receiver", "fine acquiring an older")):
        return [], [ObjectiveConstraint("durable_youth_preference_overridden", True, SourceType.EXPLICIT_CURRENT_MESSAGE.value, False, "current_request")]
    hard: list[ObjectiveConstraint] = []
    soft: list[ObjectiveConstraint] = []
    notes = " ".join(str(note) for note in owner_preferences.get("notes") or [])
    preference = _normalize(owner_preferences.get("team_build_preference"))
    if "do_not_trade_first_round_pick" in notes:
        hard.append(ObjectiveConstraint("do_not_trade_first_round_pick", True, SourceType.DURABLE_OWNER_PREFERENCE.value, True, "conversation"))
    if "younger" in notes or "youth" in preference or "prioritize_youth" in preference:
        soft.append(ObjectiveConstraint("prefers_younger_players", True, SourceType.DURABLE_OWNER_PREFERENCE.value, False, "conversation"))
    if "cap" in _normalize(owner_preferences.get("preferred_strategy")):
        soft.append(ObjectiveConstraint("preserve_cap_flexibility", True, SourceType.DURABLE_OWNER_PREFERENCE.value, False, "conversation"))
    return hard, soft


def _apply_current_overrides(
    normalized: str,
    constraints: list[ObjectiveConstraint],
    soft_preferences: list[ObjectiveConstraint],
) -> tuple[list[ObjectiveConstraint], list[ObjectiveConstraint]]:
    if _has_any(normalized, ("willing to move my first", "will move my first", "include my first")):
        constraints = [c for c in constraints if c.constraint_type != "do_not_trade_first_round_pick"]
        soft_preferences = [c for c in soft_preferences if c.constraint_type != "do_not_trade_first_round_pick"]
        soft_preferences.append(ObjectiveConstraint("allow_trade_first_round_pick", True, SourceType.EXPLICIT_CURRENT_MESSAGE.value, False, "current_request"))
    return constraints, soft_preferences


def _goals_from_constraints(
    constraints: list[ObjectiveConstraint],
    soft_preferences: list[ObjectiveConstraint],
    interpreted: InterpretedQuestion,
) -> list[str]:
    goals = []
    all_constraints = constraints + soft_preferences
    if any(c.constraint_type == "do_not_trade_first_round_pick" for c in all_constraints):
        goals.append(Goal.PRESERVE_DRAFT_CAPITAL.value)
    if any(c.constraint_type == "preserve_cap_flexibility" for c in all_constraints):
        goals.append(Goal.PRESERVE_CAP_FLEXIBILITY.value)
    if any(c.constraint_type in {"max_salary", "prefers_cheap_assets"} for c in all_constraints):
        goals.append(Goal.REDUCE_SALARY.value)
    if any(c.constraint_type in {"max_age", "prefers_younger_players"} for c in all_constraints):
        goals.append(Goal.GET_YOUNGER.value)
    if interpreted.positions:
        goals.append(Goal.IMPROVE_SPECIFIC_POSITION.value)
    return goals


def _resolve_timeframe(
    interpreted: InterpretedQuestion,
    context: AssistantRequestContext,
    conversation_state: ConversationState | None,
    normalized: str,
) -> tuple[str | None, list[int], int | None]:
    label = interpreted.timeframe.get("label") if interpreted.timeframe else None
    event = interpreted.timeframe.get("event") if interpreted.timeframe else None
    if not label and "this week" in normalized:
        label = "this_week"
    if "long term" in normalized:
        label = "long_term"
    if event == "trade_deadline" or "trade deadline" in normalized:
        label = "trade_deadline"
    target_seasons = list(interpreted.seasons or [])
    horizon_years = interpreted.timeframe.get("horizon_years") if interpreted.timeframe else None
    if label == "current_season" and context.current_season not in target_seasons:
        target_seasons.append(context.current_season)
    if label == "next_season" and context.current_season + 1 not in target_seasons:
        target_seasons.append(context.current_season + 1)
    if not label and conversation_state and conversation_state.active_timeframe:
        label = conversation_state.active_timeframe
    return label, sorted(set(target_seasons)), horizon_years


def _resolve_risk(normalized: str, owner_preferences: dict[str, Any]) -> str:
    if _has_any(normalized, ("safer", "safe option", "avoid risk", "avoid injury", "proven production", "preserve cap", "preserve picks")):
        return RiskTolerance.CONSERVATIVE.value
    if _has_any(normalized, ("take a swing", "prioritize upside", "overpay", "all in", "aggressive", "high upside")):
        return RiskTolerance.AGGRESSIVE.value
    durable = _normalize(owner_preferences.get("risk_tolerance"))
    if durable in {"conservative", "balanced", "aggressive"}:
        return durable
    if durable in {"medium", "moderate"}:
        return RiskTolerance.BALANCED.value
    return RiskTolerance.UNKNOWN.value


def _priority_weights(
    request_goal: str,
    secondary_goals: list[str],
    constraints: list[ObjectiveConstraint],
    soft_preferences: list[ObjectiveConstraint],
    interpreted: InterpretedQuestion,
    risk_tolerance: str,
) -> dict[str, str]:
    weights = {
        "immediate_production": "medium",
        "future_value": "medium",
        "age": "not_relevant",
        "cap_flexibility": "not_relevant",
        "contract_efficiency": "not_relevant",
        "positional_need": "high" if interpreted.positions else "not_relevant",
        "depth": "medium" if Goal.IMPROVE_DEPTH.value in secondary_goals or request_goal == Goal.IMPROVE_DEPTH.value else "not_relevant",
        "upside": "high" if request_goal == Goal.INCREASE_UPSIDE.value else "medium",
        "safety": "high" if risk_tolerance == RiskTolerance.CONSERVATIVE.value else "medium",
        "draft_capital": "medium",
        "roster_flexibility": "medium",
    }
    for constraint in constraints:
        if constraint.constraint_type in {"do_not_trade_first_round_pick", "allow_trade_first_round_pick"}:
            weights["draft_capital"] = "critical"
        if constraint.constraint_type in {"max_salary", "preserve_cap_flexibility"}:
            weights["cap_flexibility"] = "critical"
        if constraint.constraint_type == "max_age":
            weights["age"] = "critical"
    for preference in soft_preferences:
        if preference.constraint_type == "prefers_younger_players":
            weights["age"] = "high"
        if preference.constraint_type == "preserve_cap_flexibility":
            weights["cap_flexibility"] = "high"
    if request_goal in {Goal.WIN_NOW.value, Goal.CONTEND_THIS_SEASON.value}:
        weights["immediate_production"] = "high"
    if request_goal in {Goal.REBUILD.value, Goal.INCREASE_FUTURE_VALUE.value, Goal.PREPARE_FOR_FUTURE_SEASON.value}:
        weights["future_value"] = "high"
    return weights


def _acceptable_tradeoffs(normalized: str, owner_preferences: dict[str, Any], request_goal: str, risk_tolerance: str) -> list[str]:
    tradeoffs = []
    if _has_any(normalized, ("older veteran", "veteran receiver", "immediate production")):
        tradeoffs.append("accept_older_player_for_current_window")
    if risk_tolerance == RiskTolerance.AGGRESSIVE.value:
        tradeoffs.append("accept_more_volatility_for_upside")
    if request_goal in {Goal.WIN_NOW.value, Goal.CONTEND_THIS_SEASON.value}:
        tradeoffs.append("sacrifice_some_future_value_for_current_production")
    if owner_preferences.get("team_build_preference") and not tradeoffs:
        tradeoffs.append("respect_durable_preferences_when_current_request_is_silent")
    return tradeoffs


def _objective_ambiguities(
    request_goal: str,
    interpreted: InterpretedQuestion,
    normalized: str,
    active_goal: str | None,
) -> list[ObjectiveAmbiguity]:
    ambiguities: list[ObjectiveAmbiguity] = []
    if interpreted.confidence == Confidence.LOW.value:
        ambiguities.append(ObjectiveAmbiguity("low_confidence_interpretation", "Question interpretation is low confidence.", True, []))
    for ambiguity in interpreted.ambiguities:
        ambiguities.append(ObjectiveAmbiguity(
            ambiguity.ambiguity_type,
            ambiguity.explanation,
            ambiguity.blocking,
            ambiguity.candidates,
        ))
    if request_goal == Goal.UNCLEAR.value:
        ambiguities.append(ObjectiveAmbiguity("unclear_objective", "No clear strategic or factual request goal was detected.", True, []))
    if _has_any(normalized, ("make my team better", "improve my team")) and not interpreted.positions and not active_goal:
        ambiguities.append(ObjectiveAmbiguity("unclear_improvement_goal", "Team improvement was requested without timeframe or priority.", True, []))
    if _has_any(normalized, ("young", "younger")) and "under 25" not in normalized:
        ambiguities.append(ObjectiveAmbiguity("vague_youth_preference", "Youth preference has no exact age threshold.", False, []))
    return _dedupe_ambiguities(ambiguities)


def _strategic_conflicts(
    *,
    request_goal: str,
    secondary_goals: list[str],
    constraints: list[ObjectiveConstraint],
    interpreted_question: InterpretedQuestion,
    team_context: ObjectiveTeamContext,
    normalized: str,
) -> list[StrategicConflict]:
    conflicts: list[StrategicConflict] = []
    direction = _normalize(team_context.team_direction)
    if request_goal in {Goal.WIN_NOW.value, Goal.CONTEND_THIS_SEASON.value} and _has_any(direction, ("rebuild", "bottom", "weak")):
        conflicts.append(StrategicConflict("goal_vs_team_window", request_goal, team_context.team_direction or "", "medium", "Owner wants to contend while team context signals a weaker or rebuilding profile."))
    if request_goal == Goal.REBUILD.value and _has_any(direction, ("contend", "contender", "win")):
        conflicts.append(StrategicConflict("goal_vs_team_window", request_goal, team_context.team_direction or "", "medium", "Owner wants to rebuild while team context signals a contender profile."))
    if Goal.PRESERVE_CAP_FLEXIBILITY.value in secondary_goals and _has_any(normalized, ("expensive", "high-cost", "big salary")):
        conflicts.append(StrategicConflict("cap_goal_vs_target", Goal.PRESERVE_CAP_FLEXIBILITY.value, "high-cost target language", "low", "Request mentions preserving cap while also describing an expensive acquisition."))
    if (request_goal == Goal.GET_YOUNGER.value or Goal.GET_YOUNGER.value in secondary_goals) and _has_any(normalized, ("older veteran", "veteran receiver")):
        conflicts.append(StrategicConflict("youth_goal_vs_veteran_request", Goal.GET_YOUNGER.value, "veteran target language", "low", "Youth preference may conflict with the veteran target in this request."))
    if request_goal in {Goal.WIN_NOW.value, Goal.CONTEND_THIS_SEASON.value} and any(c.constraint_type == "do_not_trade_first_round_pick" for c in constraints):
        conflicts.append(StrategicConflict("win_now_vs_asset_protection", request_goal, "first-round pick excluded", "low", "The request protects premium draft capital while seeking immediate contention."))
    return conflicts


def _objective_confidence(
    request_goal: str,
    interpreted: InterpretedQuestion,
    ambiguities: list[ObjectiveAmbiguity],
    team_context: ObjectiveTeamContext,
) -> str:
    if request_goal == Goal.UNCLEAR.value or any(ambiguity.blocking for ambiguity in ambiguities):
        return Confidence.LOW.value
    if interpreted.confidence == Confidence.LOW.value:
        return Confidence.LOW.value
    if team_context.data_quality_warnings or ambiguities:
        return Confidence.MEDIUM.value
    return Confidence.HIGH.value


def _goal_from_preferences(owner_preferences: dict[str, Any]) -> str | None:
    preference = _normalize(owner_preferences.get("team_build_preference") or owner_preferences.get("current_focus"))
    if _has_any(preference, ("rebuild", "future")):
        return Goal.REBUILD.value
    if _has_any(preference, ("retool", "build")):
        return Goal.RETOOL.value
    if _has_any(preference, ("contend", "win")):
        return Goal.CONTEND_THIS_SEASON.value
    if _has_any(preference, ("youth", "young")):
        return Goal.GET_YOUNGER.value
    return None


def _normalize_goal(goal: Any) -> str | None:
    text = _normalize(goal)
    if not text:
        return None
    if text in {"compete", "contend", "contender"}:
        return Goal.CONTEND_THIS_SEASON.value
    if text in {"get younger", "get_younger", "younger"}:
        return Goal.GET_YOUNGER.value
    if text in {goal.value for goal in Goal}:
        return text
    if "rebuild" in text:
        return Goal.REBUILD.value
    if "retool" in text:
        return Goal.RETOOL.value
    if "win" in text or "contend" in text:
        return Goal.CONTEND_THIS_SEASON.value
    return text


def _is_factual_goal(goal: str) -> bool:
    return goal in {Goal.FACTUAL_LOOKUP.value, Goal.UNDERSTAND_RULES.value, Goal.EVALUATE_LEGALITY.value}


def _is_unclear_intent_default(goal: str) -> bool:
    return goal in {Goal.UNCLEAR.value, Goal.FACTUAL_LOOKUP.value, Goal.UNDERSTAND_RULES.value}


def _source(source_type: SourceType, statement: str, priority: int) -> ObjectiveSource:
    return ObjectiveSource(source_type.value, None, statement, priority)


def _competitive_window(team_brain: dict[str, Any]) -> str | None:
    direction = _normalize(team_brain.get("team_direction"))
    if _has_any(direction, ("contend", "win")):
        return "current_window"
    if "retool" in direction:
        return "transition_window"
    if "rebuild" in direction:
        return "future_window"
    score = _safe_float(team_brain.get("championship_window_score"))
    if score is None:
        return None
    if score >= 80:
        return "current_window"
    if score >= 60:
        return "transition_window"
    return "future_window"


def _compact_cap(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: raw.get(key)
        for key in ("cap_space", "total_salary", "salary_used", "dead_cap")
        if key in raw
    }


def _compact_draft(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        seasons = sorted({_safe_int(row.get("season")) for row in raw if isinstance(row, dict) and _safe_int(row.get("season"))})
        return {"pick_count": len(raw), "seasons": seasons[:8]}
    if isinstance(raw, dict):
        return {
            key: raw.get(key)
            for key in ("pick_count", "first_round_count", "future_pick_count", "seasons")
            if key in raw
        }
    return {}


def _compact_list(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _soft_wording(normalized: str) -> bool:
    return _has_any(normalized, ("prefer", "ideally", "try to", "lean toward", "would like"))


def _dedupe_goals(goals: list[str]) -> list[str]:
    out = []
    for goal in goals:
        if goal and goal not in out:
            out.append(goal)
    return out


def _dedupe_constraints(constraints: list[ObjectiveConstraint]) -> list[ObjectiveConstraint]:
    out: list[ObjectiveConstraint] = []
    seen = set()
    for constraint in constraints:
        key = (constraint.constraint_type, str(constraint.value), constraint.source, constraint.hard, constraint.scope)
        if key not in seen:
            out.append(constraint)
            seen.add(key)
    return out


def _dedupe_ambiguities(ambiguities: list[ObjectiveAmbiguity]) -> list[ObjectiveAmbiguity]:
    out: list[ObjectiveAmbiguity] = []
    seen = set()
    for ambiguity in ambiguities:
        key = (ambiguity.ambiguity_type, ambiguity.explanation, ambiguity.blocking)
        if key not in seen:
            out.append(ambiguity)
            seen.add(key)
    return out


def _has_any(normalized: str, markers: tuple[str, ...]) -> bool:
    return any(marker in normalized for marker in markers)


def _normalize(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("don't", "dont").replace("i'm", "im")
    text = re.sub(r"[^a-z0-9.$\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except Exception:
        return None
