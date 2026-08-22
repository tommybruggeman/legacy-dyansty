from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable

from gm_assistant.calculations import CalculationPacket, CalculationResult
from gm_assistant.conversation_state import ConversationState
from gm_assistant.evidence import EvidencePacket, PlayerEvidence, TeamEvidence
from gm_assistant.interpretation import AssetRef, InterpretedQuestion
from gm_assistant.objective import Goal, ObjectiveConstraint, OwnerObjective
from gm_assistant.planning import DecisionPlan
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.rules import RulesEvaluation


DECISION_VERSION = "gm_decision_output.v1"
MAX_ALTERNATIVES = 5
MAX_FACTORS = 8
MAX_REJECTED = 8
MAX_CANDIDATES = 12


@dataclass
class GMIntent:
    intent: str
    owner_team_name: str
    primary_player: str | None = None
    comparison_player: str | None = None
    team_goal: str | None = None
    topic: str | None = None
    original_question: str | None = None
    resolved_question: str | None = None
    previous_recommendation: str | None = None

    def to_dict(self):
        return asdict(self)


class DecisionType(str, Enum):
    FACTUAL_RESPONSE = "factual_response"
    RULES_RESPONSE = "rules_response"
    PLAYER_EVALUATION = "player_evaluation"
    PLAYER_COMPARISON = "player_comparison"
    ROSTER_STRATEGY = "roster_strategy"
    TRADE_EVALUATION = "trade_evaluation"
    TRADE_DISCOVERY = "trade_discovery"
    TRADE_CONSTRUCTION = "trade_construction"
    DRAFT_RECOMMENDATION = "draft_recommendation"
    DRAFT_PICK_EVALUATION = "draft_pick_evaluation"
    FREE_AGENT_RECOMMENDATION = "free_agent_recommendation"
    CONTRACT_DECISION = "contract_decision"
    SALARY_CAP_STRATEGY = "salary_cap_strategy"
    LINEUP_DECISION = "lineup_decision"
    ROSTER_MOVE_DECISION = "roster_move_decision"
    LONG_TERM_PLAN = "long_term_plan"
    TEAM_COMPARISON = "team_comparison"
    LEAGUE_ANALYSIS = "league_analysis"
    GENERAL_CONVERSATION = "general_conversation"
    NO_DECISION = "no_decision"
    UNSUPPORTED = "unsupported"


class DecisionAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    COUNTER = "counter"
    HOLD = "hold"
    PURSUE = "pursue"
    DO_NOT_PURSUE = "do_not_pursue"
    REQUEST_MORE_INFORMATION = "request_more_information"
    ACQUIRE = "acquire"
    RETAIN = "retain"
    SELL = "sell"
    AVOID = "avoid"
    MONITOR = "monitor"
    PREFER_PLAYER_A = "prefer_player_a"
    PREFER_PLAYER_B = "prefer_player_b"
    NO_CLEAR_PREFERENCE = "no_clear_preference"
    DRAFT_PLAYER = "draft_player"
    TRADE_DOWN = "trade_down"
    TRADE_UP = "trade_up"
    HOLD_PICK = "hold_pick"
    SELL_PICK = "sell_pick"
    BEST_PLAYER_AVAILABLE = "best_player_available"
    EXTEND = "extend"
    DO_NOT_EXTEND = "do_not_extend"
    RELEASE = "release"
    RESTRUCTURE = "restructure"
    WAIT = "wait"
    REQUEST_TERMS = "request_terms"
    START = "start"
    BENCH = "bench"
    FLEX = "flex"
    CONTEND = "contend"
    REBUILD = "rebuild"
    RETOOL = "retool"
    STAY_BALANCED = "stay_balanced"
    PRESERVE_FLEXIBILITY = "preserve_flexibility"
    INCREASE_FUTURE_ASSETS = "increase_future_assets"
    INCREASE_CURRENT_STRENGTH = "increase_current_strength"
    NO_RECOMMENDATION = "no_recommendation"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"


class RecommendationStatus(str, Enum):
    RECOMMENDED = "recommended"
    RECOMMENDED_WITH_CONDITIONS = "recommended_with_conditions"
    NOT_RECOMMENDED = "not_recommended"
    NO_CLEAR_PREFERENCE = "no_clear_preference"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True)
class RecommendationOption:
    option_id: str
    label: str
    action: str
    rank: int | None
    score: float | None
    score_scale: str | None
    explanation: str
    related_entity_ids: list[str] = field(default_factory=list)
    objective_fit: dict[str, Any] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    rule_status: str | None = None
    calculation_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: str = "medium"


@dataclass(frozen=True)
class RejectedOption:
    option_id: str
    label: str
    rejection_reason: str
    related_entity_ids: list[str] = field(default_factory=list)
    blocking_rule_ref: str | None = None


@dataclass(frozen=True)
class DecisionFactor:
    factor_type: str
    direction: str
    importance: str
    explanation: str
    source_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionRuleConstraint:
    rule_type: str
    status: str
    explanation: str
    blocking: bool
    source_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionCalculationRef:
    calculation_type: str
    status: str
    output_key: str
    value_summary: Any
    exact: bool
    estimated: bool


@dataclass(frozen=True)
class DecisionRisk:
    risk_type: str
    severity: str
    explanation: str
    related_entity_ids: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionTradeoff:
    gives_up: str
    receives: str
    importance: str
    explanation: str


@dataclass(frozen=True)
class DecisionCondition:
    condition_type: str
    explanation: str
    satisfied: bool | None
    blocking: bool
    related_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionUnresolvedItem:
    item_type: str
    explanation: str
    blocking: bool
    missing_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DecisionOutput:
    decision_type: str
    action: str
    recommendation_status: str
    primary_recommendation: RecommendationOption | None = None
    alternatives: list[RecommendationOption] = field(default_factory=list)
    rejected_options: list[RejectedOption] = field(default_factory=list)
    reasoning_factors: list[DecisionFactor] = field(default_factory=list)
    rule_constraints: list[DecisionRuleConstraint] = field(default_factory=list)
    supporting_calculations: list[DecisionCalculationRef] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    risks: list[DecisionRisk] = field(default_factory=list)
    tradeoffs: list[DecisionTradeoff] = field(default_factory=list)
    conditions: list[DecisionCondition] = field(default_factory=list)
    unresolved_questions: list[DecisionUnresolvedItem] = field(default_factory=list)
    actionable_now: bool = False
    recommendation_complete: bool = False
    reduced_mode: bool = False
    confidence: str = "medium"
    decision_version: str = DECISION_VERSION

    def to_packet(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _DecisionContext:
    context: AssistantRequestContext
    conversation_state: ConversationState | None
    interpreted_question: InterpretedQuestion
    owner_objective: OwnerObjective
    decision_plan: DecisionPlan
    evidence_packet: EvidencePacket
    rules_evaluation: RulesEvaluation
    calculation_packet: CalculationPacket


DecisionHandler = Callable[[_DecisionContext], DecisionOutput]


def build_decision_output(
    *,
    context: AssistantRequestContext,
    conversation_state: ConversationState | None,
    interpreted_question: InterpretedQuestion,
    owner_objective: OwnerObjective,
    decision_plan: DecisionPlan,
    evidence_packet: EvidencePacket,
    rules_evaluation: RulesEvaluation,
    calculation_packet: CalculationPacket,
) -> DecisionOutput:
    try:
        _validate_alignment(context, conversation_state, decision_plan, evidence_packet, rules_evaluation, calculation_packet)
    except Exception as exc:
        return _blocked_output(
            decision_type=_decision_type_for_plan(decision_plan),
            action=DecisionAction.REQUEST_MORE_INFORMATION.value,
            explanation=str(exc),
            missing_refs=["request_context", "conversation_state", "evidence_packet", "rules_evaluation", "calculation_packet"],
        )

    ctx = _DecisionContext(
        context,
        conversation_state,
        interpreted_question,
        owner_objective,
        decision_plan,
        evidence_packet,
        rules_evaluation,
        calculation_packet,
    )
    if rules_evaluation.overall_status in {"blocked", "failed"}:
        return _blocked_output(_decision_type_for_plan(decision_plan), DecisionAction.REQUEST_MORE_INFORMATION.value, "Rules evaluation is blocked or failed.", ["rules_evaluation"])
    if evidence_packet.execution_status in {"blocked", "failed"}:
        return _blocked_output(_decision_type_for_plan(decision_plan), DecisionAction.REQUEST_MORE_INFORMATION.value, "Required evidence is blocked or failed.", ["evidence_packet"])
    handler = DECISION_ENGINES.get(decision_plan.decision_engine, _unsupported_decision)
    try:
        return _finalize(handler(ctx), ctx)
    except Exception:
        return DecisionOutput(
            decision_type=_decision_type_for_plan(decision_plan),
            action=DecisionAction.REQUEST_MORE_INFORMATION.value,
            recommendation_status=RecommendationStatus.FAILED.value,
            unresolved_questions=[DecisionUnresolvedItem("system_error", "Decision engine failed safely.", True, [])],
            actionable_now=False,
            recommendation_complete=False,
            reduced_mode=True,
            confidence="low",
        )


def build_decision_packet(decision_output: DecisionOutput | None) -> dict[str, Any]:
    if not decision_output:
        return {}
    packet = decision_output.to_packet()
    packet.pop("decision_version", None)
    return _compact(packet)


def _factual_decision(ctx: _DecisionContext) -> DecisionOutput:
    return DecisionOutput(
        decision_type=DecisionType.FACTUAL_RESPONSE.value,
        action=DecisionAction.NOT_APPLICABLE.value,
        recommendation_status=RecommendationStatus.NOT_APPLICABLE.value,
        reasoning_factors=[_factor("request_type", "neutral", "high", "Factual lookups do not require a strategic recommendation.", ["decision_plan"])],
        actionable_now=False,
        recommendation_complete=True,
        confidence="high" if ctx.evidence_packet.required_evidence_complete else "medium",
    )


def _rules_decision(ctx: _DecisionContext) -> DecisionOutput:
    status = ctx.rules_evaluation.overall_status
    action = DecisionAction.NO_RECOMMENDATION.value
    rec_status = RecommendationStatus.NOT_APPLICABLE.value
    complete = ctx.rules_evaluation.rules_complete
    actionable = False
    if status == "illegal":
        action = DecisionAction.REJECT.value
        rec_status = RecommendationStatus.NOT_RECOMMENDED.value
        complete = True
    elif status == "conditionally_legal":
        rec_status = RecommendationStatus.RECOMMENDED_WITH_CONDITIONS.value
    elif status == "unverifiable":
        rec_status = RecommendationStatus.INSUFFICIENT_INFORMATION.value
    elif status == "legal":
        rec_status = RecommendationStatus.NOT_APPLICABLE.value
    return DecisionOutput(
        decision_type=DecisionType.RULES_RESPONSE.value,
        action=action,
        recommendation_status=rec_status,
        primary_recommendation=_option("rules", action, action, 1, None, "Rules response preserves the deterministic legality result.", rule_status=status, confidence=ctx.rules_evaluation.confidence) if rec_status != RecommendationStatus.NOT_APPLICABLE.value else None,
        rule_constraints=_rule_constraints(ctx.rules_evaluation),
        conditions=_conditions_from_rules(ctx.rules_evaluation),
        unresolved_questions=_unresolved_from_rules(ctx.rules_evaluation),
        actionable_now=actionable,
        recommendation_complete=complete,
        reduced_mode=ctx.rules_evaluation.reduced_mode,
        confidence=ctx.rules_evaluation.confidence,
    )


def _player_decision(ctx: _DecisionContext) -> DecisionOutput:
    gated = _rule_or_calculation_gate(ctx)
    if gated:
        return gated
    players = ctx.evidence_packet.player_evidence
    if not players:
        return _insufficient(ctx, DecisionType.PLAYER_EVALUATION.value, "Player evidence is missing.", ["player_evidence"])
    player = players[0]
    value = _player_value(player)
    efficiency = _result_value(ctx.calculation_packet, "contract_efficiency")
    age = _safe_float(player.age)
    score, fit = _objective_fit_score(ctx, value=value, age=age, cap_delta=None)
    action = DecisionAction.MONITOR.value
    if value is None:
        return _insufficient(ctx, DecisionType.PLAYER_EVALUATION.value, "Stored player value is unavailable.", ["stored_player_value"])
    if player.fantasy_team_id == ctx.context.league_team_id:
        if value >= 65 and not _poor_contract(efficiency, player.player_id):
            action = DecisionAction.RETAIN.value
        elif _poor_contract(efficiency, player.player_id):
            action = DecisionAction.SELL.value
        elif value < 40:
            action = DecisionAction.AVOID.value
        else:
            action = DecisionAction.HOLD.value
    else:
        action = DecisionAction.ACQUIRE.value if value >= 65 else DecisionAction.MONITOR.value if value >= 45 else DecisionAction.AVOID.value
    explanation = f"{action.replace('_', ' ').title()} is the deterministic player decision from stored value, contract fit, and objective fit."
    option = _option("player_primary", player.canonical_name or player.player_id, action, 1, score, explanation, related=[player.player_id], objective_fit=fit, evidence_refs=["player_evidence"], calculation_refs=_calculation_types(ctx.calculation_packet), confidence=_confidence(ctx))
    return DecisionOutput(
        decision_type=DecisionType.PLAYER_EVALUATION.value,
        action=action,
        recommendation_status=RecommendationStatus.RECOMMENDED.value if action not in {DecisionAction.AVOID.value} else RecommendationStatus.NOT_RECOMMENDED.value,
        primary_recommendation=option,
        reasoning_factors=_objective_factors(ctx, fit) + [_factor("stored_value", "supports" if value >= 55 else "opposes", "high", f"Stored value is {value}.", ["player_evidence"])],
        supporting_calculations=_calculation_refs(ctx.calculation_packet),
        evidence_refs=["player_evidence"],
        risks=_risks_from_player(player),
        actionable_now=action in {DecisionAction.RETAIN.value, DecisionAction.HOLD.value},
        recommendation_complete=True,
        reduced_mode=ctx.calculation_packet.reduced_mode,
        confidence=_confidence(ctx),
    )


def _comparison_decision(ctx: _DecisionContext) -> DecisionOutput:
    gated = _rule_or_calculation_gate(ctx)
    if gated:
        return gated
    players = ctx.evidence_packet.player_evidence[:2]
    if len(players) < 2:
        return _insufficient(ctx, DecisionType.PLAYER_COMPARISON.value, "Two verified players are required for comparison.", ["player_evidence"])
    values = [_player_value(player) for player in players]
    if any(value is None for value in values):
        return _insufficient(ctx, DecisionType.PLAYER_COMPARISON.value, "Equivalent stored value is required for both compared players.", ["stored_player_value"])
    score_a, fit_a = _objective_fit_score(ctx, value=values[0], age=players[0].age, cap_delta=None)
    score_b, fit_b = _objective_fit_score(ctx, value=values[1], age=players[1].age, cap_delta=None)
    diff = score_a - score_b
    if abs(diff) < 4:
        action = DecisionAction.NO_CLEAR_PREFERENCE.value
        status = RecommendationStatus.NO_CLEAR_PREFERENCE.value
        primary = None
    else:
        action = DecisionAction.PREFER_PLAYER_A.value if diff > 0 else DecisionAction.PREFER_PLAYER_B.value
        status = RecommendationStatus.RECOMMENDED.value
        winner = players[0] if diff > 0 else players[1]
        primary = _option("comparison_winner", winner.canonical_name or winner.player_id, action, 1, max(score_a, score_b), "Preferred player has the stronger deterministic objective-fit score.", related=[winner.player_id], objective_fit=fit_a if diff > 0 else fit_b, evidence_refs=["player_evidence"], calculation_refs=_calculation_types(ctx.calculation_packet), confidence=_confidence(ctx))
    alternatives = [
        _option("player_a", players[0].canonical_name or players[0].player_id, DecisionAction.PREFER_PLAYER_A.value, 1, score_a, "Player A comparison option.", related=[players[0].player_id], objective_fit=fit_a),
        _option("player_b", players[1].canonical_name or players[1].player_id, DecisionAction.PREFER_PLAYER_B.value, 2, score_b, "Player B comparison option.", related=[players[1].player_id], objective_fit=fit_b),
    ]
    return DecisionOutput(DecisionType.PLAYER_COMPARISON.value, action, status, primary, alternatives, reasoning_factors=[_factor("value_comparison", "supports" if abs(diff) >= 4 else "neutral", "high", f"Objective-fit score difference is {round(diff, 2)}.", ["calculation_packet"])], supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=["player_evidence"], actionable_now=False, recommendation_complete=True, reduced_mode=ctx.calculation_packet.reduced_mode, confidence=_confidence(ctx))


def _roster_strategy_decision(ctx: _DecisionContext) -> DecisionOutput:
    gated = _rule_or_calculation_gate(ctx, allow_reduced=True)
    if gated:
        return gated
    team = _team(ctx)
    if not team:
        return _insufficient(ctx, DecisionType.ROSTER_STRATEGY.value, "Team-brain evidence is missing.", ["team_evidence"])
    direction = str(team.team_brain_summary.get("team_direction") or "").lower()
    requested = ctx.owner_objective.request_goal
    action = DecisionAction.STAY_BALANCED.value
    if requested in {Goal.WIN_NOW.value, Goal.CONTEND_THIS_SEASON.value}:
        action = DecisionAction.CONTEND.value
    elif requested in {Goal.REBUILD.value, Goal.ACQUIRE_DRAFT_CAPITAL.value, Goal.INCREASE_FUTURE_VALUE.value}:
        action = DecisionAction.REBUILD.value
    elif requested in {Goal.RETOOL.value, Goal.GET_YOUNGER.value}:
        action = DecisionAction.RETOOL.value
    elif "contend" in direction:
        action = DecisionAction.CONTEND.value
    elif "rebuild" in direction:
        action = DecisionAction.REBUILD.value
    elif "retool" in direction:
        action = DecisionAction.RETOOL.value
    conflict = bool(ctx.owner_objective.strategic_conflicts)
    option = _option("roster_strategy", action.replace("_", " ").title(), action, 1, None, "Roster strategy follows the explicit owner objective first, then stored team-brain direction.", related=[ctx.context.league_team_id], objective_fit={"request_goal": requested, "team_brain_direction": direction}, evidence_refs=["team_evidence"], calculation_refs=_calculation_types(ctx.calculation_packet), confidence=_confidence(ctx))
    factors = [_factor("owner_objective", "supports", "critical", f"Owner request goal is {requested}.", ["owner_objective"]), _factor("team_brain_direction", "supports" if not conflict else "uncertain", "medium", f"Stored team direction is {direction or 'unavailable'}.", ["team_evidence"])]
    risks = [DecisionRisk("strategic_conflict", "medium", conflict.explanation, [], ["owner_objective"]) for conflict in ctx.owner_objective.strategic_conflicts]
    return DecisionOutput(DecisionType.ROSTER_STRATEGY.value, action, RecommendationStatus.RECOMMENDED_WITH_CONDITIONS.value if conflict else RecommendationStatus.RECOMMENDED.value, option, reasoning_factors=factors, supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=["team_evidence"], risks=risks, actionable_now=False, recommendation_complete=True, reduced_mode=ctx.calculation_packet.reduced_mode or conflict, confidence="medium" if conflict else _confidence(ctx))


def _trade_evaluation_decision(ctx: _DecisionContext) -> DecisionOutput:
    illegal = _illegal_gate(ctx, DecisionType.TRADE_EVALUATION.value)
    if illegal:
        return illegal
    hard = _hard_constraint_gate(ctx, DecisionType.TRADE_EVALUATION.value)
    if hard:
        return hard
    calc_gate = _calculation_gate(ctx, DecisionType.TRADE_EVALUATION.value, missing_message="Required trade calculations are incomplete.")
    if calc_gate:
        return calc_gate
    value_delta = _dict_number(_result_value(ctx.calculation_packet, "trade_value_delta") or _result_value(ctx.calculation_packet, "trade_fairness"), "value_delta")
    post_cap = _numeric_result(ctx.calculation_packet, "trade_cap_delta")
    roster_value = _result_value(ctx.calculation_packet, "trade_roster_delta")
    factors: list[DecisionFactor] = []
    if value_delta is None:
        return _insufficient(ctx, DecisionType.TRADE_EVALUATION.value, "Trade value is unavailable or partial.", ["trade_value_delta"])
    factors.append(_factor("verified_value_delta", "supports" if value_delta > 0 else "opposes" if value_delta < 0 else "neutral", "high", f"Verified value delta is {value_delta}.", ["calculation_packet.trade_value_delta"]))
    if post_cap is not None:
        factors.append(_factor("post_trade_cap", "supports" if post_cap >= 0 else "opposes", "high", f"Projected available cap after the move is {post_cap}.", ["calculation_packet.trade_cap_delta"]))
    if isinstance(roster_value, dict):
        factors.append(_factor("roster_delta", "neutral", "medium", f"Projected post-transaction roster count is {roster_value.get('post_count')}.", ["calculation_packet.trade_roster_delta"]))
    action = DecisionAction.ACCEPT.value if value_delta >= 5 and (post_cap is None or post_cap >= 0) else DecisionAction.REJECT.value if value_delta <= -5 or (post_cap is not None and post_cap < 0) else DecisionAction.COUNTER.value
    status = RecommendationStatus.RECOMMENDED.value if action == DecisionAction.ACCEPT.value else RecommendationStatus.NOT_RECOMMENDED.value if action == DecisionAction.REJECT.value else RecommendationStatus.RECOMMENDED_WITH_CONDITIONS.value
    if ctx.rules_evaluation.overall_status == "conditionally_legal":
        status = RecommendationStatus.RECOMMENDED_WITH_CONDITIONS.value if action != DecisionAction.REJECT.value else status
    option = _option("trade_primary", action.title(), action, 1, _bounded_score(50 + value_delta), "Trade decision uses verified value delta, cap impact, roster impact, objective fit, and rule status.", objective_fit={"active_goal": ctx.owner_objective.active_strategic_goal, "value_delta": value_delta}, calculation_refs=_calculation_types(ctx.calculation_packet), evidence_refs=["player_evidence", "contract_evidence", "team_evidence"], confidence=_confidence(ctx))
    alternatives = [_option("trade_alternative_counter", "Counter", DecisionAction.COUNTER.value, 2, None, "Counter remains viable when value is close or legality/cap conditions need cleanup.")] if action != DecisionAction.COUNTER.value else []
    return DecisionOutput(DecisionType.TRADE_EVALUATION.value, action, status, option, alternatives[:MAX_ALTERNATIVES], reasoning_factors=factors, rule_constraints=_rule_constraints(ctx.rules_evaluation), supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=["evidence_packet"], conditions=_conditions_from_rules(ctx.rules_evaluation), tradeoffs=_tradeoffs_from_value(value_delta), actionable_now=action == DecisionAction.ACCEPT.value and ctx.rules_evaluation.overall_status in {"legal", "not_applicable"} and ctx.calculation_packet.required_calculations_complete, recommendation_complete=True, reduced_mode=ctx.rules_evaluation.reduced_mode or ctx.calculation_packet.reduced_mode, confidence=_confidence(ctx))


def _bounded_list_decision(ctx: _DecisionContext, decision_type: str, action: str, label: str) -> DecisionOutput:
    gated = _rule_or_calculation_gate(ctx, allow_reduced=True)
    if gated:
        return gated
    candidates = ctx.evidence_packet.player_evidence[:MAX_CANDIDATES]
    if not candidates:
        return _insufficient(ctx, decision_type, f"{label} requires verified candidate evidence.", ["player_evidence"])
    options = []
    for index, player in enumerate(candidates):
        if player.fantasy_team_id == ctx.context.league_team_id and decision_type in {DecisionType.TRADE_DISCOVERY.value, DecisionType.FREE_AGENT_RECOMMENDATION.value}:
            continue
        value = _player_value(player)
        if value is None:
            continue
        score, fit = _objective_fit_score(ctx, value=value, age=player.age, cap_delta=None)
        options.append(_option(f"candidate_{player.player_id}", player.canonical_name or player.player_id, action, index + 1, score, "Candidate ranking uses verified stored player value and objective fit only.", related=[player.player_id], objective_fit=fit, evidence_refs=["player_evidence"], confidence=_confidence(ctx)))
    options = sorted(options, key=lambda item: item.score or 0, reverse=True)[:MAX_ALTERNATIVES]
    if not options:
        return _insufficient(ctx, decision_type, "No verified available candidates have compatible value evidence.", ["candidate_value"])
    primary = options[0]
    return DecisionOutput(decision_type, action, RecommendationStatus.RECOMMENDED.value, primary, options[1:], reasoning_factors=[_factor("candidate_pool", "supports", "high", f"{len(options)} bounded candidates evaluated.", ["player_evidence"])], supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=["player_evidence"], actionable_now=False, recommendation_complete=True, reduced_mode=True, confidence="medium")


def _trade_discovery_decision(ctx: _DecisionContext) -> DecisionOutput:
    return _bounded_list_decision(ctx, DecisionType.TRADE_DISCOVERY.value, DecisionAction.PURSUE.value, "Trade discovery")


def _trade_construction_decision(ctx: _DecisionContext) -> DecisionOutput:
    base = _trade_evaluation_decision(ctx)
    if base.recommendation_status in {RecommendationStatus.BLOCKED.value, RecommendationStatus.INSUFFICIENT_INFORMATION.value, RecommendationStatus.NOT_RECOMMENDED.value}:
        return DecisionOutput(DecisionType.TRADE_CONSTRUCTION.value, base.action, base.recommendation_status, base.primary_recommendation, base.alternatives, base.rejected_options, base.reasoning_factors, base.rule_constraints, base.supporting_calculations, base.evidence_refs, base.risks, base.tradeoffs, base.conditions, base.unresolved_questions, False, base.recommendation_complete, base.reduced_mode, base.confidence)
    return DecisionOutput(DecisionType.TRADE_CONSTRUCTION.value, DecisionAction.COUNTER.value, RecommendationStatus.RECOMMENDED_WITH_CONDITIONS.value, _option("bounded_offer", "Bounded offer structure", DecisionAction.COUNTER.value, 1, None, "A bounded offer can be proposed from owned assets only; acceptance probability is not claimed.", calculation_refs=_calculation_types(ctx.calculation_packet), evidence_refs=["evidence_packet"], confidence="medium"), alternatives=[_option("walk_away", "Walk-away threshold", DecisionAction.HOLD.value, 2, None, "Hold if required value, cap, or legality support is missing.")], reasoning_factors=base.reasoning_factors, supporting_calculations=base.supporting_calculations, evidence_refs=base.evidence_refs, conditions=base.conditions + [DecisionCondition("acceptance_probability_unknown", "No deterministic owner-acceptance model exists.", None, False, ["trade_realism"])], actionable_now=False, recommendation_complete=True, reduced_mode=True, confidence="medium")


def _draft_recommendation_decision(ctx: _DecisionContext) -> DecisionOutput:
    if not ctx.evidence_packet.draft_pick_evidence:
        return _insufficient(ctx, DecisionType.DRAFT_RECOMMENDATION.value, "Draft pick ownership or slot evidence is missing.", ["draft_pick_evidence"])
    return _bounded_list_decision(ctx, DecisionType.DRAFT_RECOMMENDATION.value, DecisionAction.DRAFT_PLAYER.value, "Draft recommendation")


def _draft_pick_decision(ctx: _DecisionContext) -> DecisionOutput:
    gated = _rule_or_calculation_gate(ctx, allow_reduced=True)
    if gated:
        return gated
    if not ctx.evidence_packet.draft_pick_evidence:
        return _insufficient(ctx, DecisionType.DRAFT_PICK_EVALUATION.value, "Draft pick evidence is missing.", ["draft_pick_evidence"])
    if _has_unresolved(ctx.calculation_packet, "pick_value"):
        return _insufficient(ctx, DecisionType.DRAFT_PICK_EVALUATION.value, "Pick value is unresolved; no hidden public chart is used.", ["verified_pick_value"])
    action = DecisionAction.HOLD_PICK.value if ctx.owner_objective.request_goal in {Goal.PRESERVE_DRAFT_CAPITAL.value, Goal.REBUILD.value} else DecisionAction.WAIT.value
    return DecisionOutput(DecisionType.DRAFT_PICK_EVALUATION.value, action, RecommendationStatus.RECOMMENDED.value, _option("pick_decision", action.replace("_", " ").title(), action, 1, None, "Pick decision uses verified ownership and objective fit; future slot uncertainty is preserved.", evidence_refs=["draft_pick_evidence"], calculation_refs=_calculation_types(ctx.calculation_packet), confidence=_confidence(ctx)), supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=["draft_pick_evidence"], actionable_now=False, recommendation_complete=True, reduced_mode=ctx.calculation_packet.reduced_mode, confidence=_confidence(ctx))


def _free_agent_decision(ctx: _DecisionContext) -> DecisionOutput:
    if not ctx.evidence_packet.free_agent_evidence and not ctx.evidence_packet.player_evidence:
        return _insufficient(ctx, DecisionType.FREE_AGENT_RECOMMENDATION.value, "Verified free-agent availability evidence is missing.", ["free_agent_evidence"])
    if ctx.rules_evaluation.overall_status == "illegal":
        return _illegal_gate(ctx, DecisionType.FREE_AGENT_RECOMMENDATION.value) or _unsupported_decision(ctx)
    return _bounded_list_decision(ctx, DecisionType.FREE_AGENT_RECOMMENDATION.value, DecisionAction.ACQUIRE.value, "Free-agent recommendation")


def _contract_decision(ctx: _DecisionContext) -> DecisionOutput:
    if ctx.decision_plan.response_mode == "direct_factual" and not ctx.decision_plan.calculation_requests:
        if not ctx.evidence_packet.contract_evidence:
            return _insufficient(ctx, DecisionType.FACTUAL_RESPONSE.value, "Contract evidence is missing.", ["contract_evidence"])
        return DecisionOutput(
            decision_type=DecisionType.FACTUAL_RESPONSE.value,
            action=DecisionAction.NOT_APPLICABLE.value,
            recommendation_status=RecommendationStatus.NOT_APPLICABLE.value,
            reasoning_factors=[_factor("request_type", "neutral", "high", "Factual contract lookups do not require extension terms.", ["decision_plan", "contract_evidence"])],
            evidence_refs=["contract_evidence"],
            actionable_now=False,
            recommendation_complete=True,
            reduced_mode=ctx.evidence_packet.reduced_mode,
            confidence="high",
        )
    illegal = _illegal_gate(ctx, DecisionType.CONTRACT_DECISION.value)
    if illegal:
        return illegal
    if _has_unresolved(ctx.calculation_packet, "extension_schedule"):
        return _insufficient(ctx, DecisionType.CONTRACT_DECISION.value, "Extension terms are required; Stage 9 will not invent market terms.", ["extension_schedule"])
    efficiency = _result_value(ctx.calculation_packet, "contract_efficiency")
    action = DecisionAction.WAIT.value
    if isinstance(efficiency, dict) and efficiency:
        first = next(iter(efficiency.values()))
        if _safe_float(first) and float(first) >= 5:
            action = DecisionAction.EXTEND.value
        elif _safe_float(first) and float(first) < 2:
            action = DecisionAction.DO_NOT_EXTEND.value
    elif _result_value(ctx.calculation_packet, "release_savings") is not None:
        action = DecisionAction.RELEASE.value if _numeric_result(ctx.calculation_packet, "release_savings") and _numeric_result(ctx.calculation_packet, "release_savings") > 0 else DecisionAction.RETAIN.value
    else:
        action = DecisionAction.REQUEST_TERMS.value
        return _insufficient(ctx, DecisionType.CONTRACT_DECISION.value, "Contract decision needs efficiency, release savings, or supplied extension terms.", ["contract_calculations"], action=action)
    return DecisionOutput(DecisionType.CONTRACT_DECISION.value, action, RecommendationStatus.RECOMMENDED.value if action != DecisionAction.DO_NOT_EXTEND.value else RecommendationStatus.NOT_RECOMMENDED.value, _option("contract_decision", action.replace("_", " ").title(), action, 1, None, "Contract decision uses supplied terms and verified contract calculations only.", calculation_refs=_calculation_types(ctx.calculation_packet), evidence_refs=["contract_evidence"], confidence=_confidence(ctx)), supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=["contract_evidence"], actionable_now=False, recommendation_complete=True, reduced_mode=ctx.calculation_packet.reduced_mode, confidence=_confidence(ctx))


def _salary_cap_decision(ctx: _DecisionContext) -> DecisionOutput:
    if ctx.decision_plan.response_mode == "direct_factual":
        if not ctx.calculation_packet.results:
            return _insufficient(ctx, DecisionType.FACTUAL_RESPONSE.value, "Cap lookup requires verified cap calculations.", ["calculation_packet"])
        return DecisionOutput(
            decision_type=DecisionType.FACTUAL_RESPONSE.value,
            action=DecisionAction.NOT_APPLICABLE.value,
            recommendation_status=RecommendationStatus.NOT_APPLICABLE.value,
            reasoning_factors=[_factor("request_type", "neutral", "high", "Direct cap lookups do not require a strategic recommendation.", ["cap_evidence", "calculation_packet"])],
            supporting_calculations=_calculation_refs(ctx.calculation_packet),
            evidence_refs=["cap_evidence"],
            actionable_now=False,
            recommendation_complete=True,
            reduced_mode=ctx.calculation_packet.reduced_mode,
            confidence=_confidence(ctx),
        )
    if _has_unresolved(ctx.calculation_packet, "release_savings") or not ctx.calculation_packet.results:
        return _insufficient(ctx, DecisionType.SALARY_CAP_STRATEGY.value, "Cap strategy requires verified savings or cap calculations.", ["calculation_packet"])
    action = DecisionAction.PRESERVE_FLEXIBILITY.value if ctx.owner_objective.request_goal == Goal.PRESERVE_CAP_FLEXIBILITY.value else DecisionAction.INCREASE_CURRENT_STRENGTH.value
    return DecisionOutput(DecisionType.SALARY_CAP_STRATEGY.value, action, RecommendationStatus.RECOMMENDED.value, _option("cap_strategy", action.replace("_", " ").title(), action, 1, None, "Cap strategy uses verified cap and savings calculations; no roster action is executed.", calculation_refs=_calculation_types(ctx.calculation_packet), confidence=_confidence(ctx)), supporting_calculations=_calculation_refs(ctx.calculation_packet), actionable_now=False, recommendation_complete=True, reduced_mode=ctx.calculation_packet.reduced_mode, confidence=_confidence(ctx))


def _lineup_decision(ctx: _DecisionContext) -> DecisionOutput:
    illegal = _illegal_gate(ctx, DecisionType.LINEUP_DECISION.value)
    if illegal:
        return illegal
    projection = _result_value(ctx.calculation_packet, "lineup_projection") or _result_value(ctx.calculation_packet, "floor_ceiling")
    if projection is None:
        return _insufficient(ctx, DecisionType.LINEUP_DECISION.value, "Trusted lineup projections are unavailable.", ["lineup_projection"])
    action = DecisionAction.START.value if _dict_number(projection, "projected_points") is not None else DecisionAction.NO_CLEAR_PREFERENCE.value
    status = RecommendationStatus.RECOMMENDED.value if action == DecisionAction.START.value else RecommendationStatus.NO_CLEAR_PREFERENCE.value
    return DecisionOutput(DecisionType.LINEUP_DECISION.value, action, status, _option("lineup_decision", action.title(), action, 1, _dict_number(projection, "projected_points"), "Lineup decision uses trusted projection fields only.", calculation_refs=_calculation_types(ctx.calculation_packet), evidence_refs=["lineup_evidence"], confidence=_confidence(ctx)), supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=["lineup_evidence"], actionable_now=ctx.rules_evaluation.overall_status in {"legal", "not_applicable"}, recommendation_complete=True, reduced_mode=ctx.calculation_packet.reduced_mode, confidence=_confidence(ctx))


def _roster_move_decision(ctx: _DecisionContext) -> DecisionOutput:
    illegal = _illegal_gate(ctx, DecisionType.ROSTER_MOVE_DECISION.value)
    if illegal:
        return illegal
    raw = (ctx.interpreted_question.raw_question or "").lower()
    if "what happens" in raw and "cap" in raw:
        if not ctx.calculation_packet.results:
            return _insufficient(ctx, DecisionType.FACTUAL_RESPONSE.value, "Release-impact facts require verified calculations.", ["calculation_packet"])
        return DecisionOutput(
            decision_type=DecisionType.FACTUAL_RESPONSE.value,
            action=DecisionAction.NOT_APPLICABLE.value,
            recommendation_status=RecommendationStatus.NOT_APPLICABLE.value,
            reasoning_factors=[_factor("request_type", "neutral", "high", "Release-impact lookup does not execute or recommend the roster move.", ["contract_evidence", "cap_evidence", "calculation_packet"])],
            supporting_calculations=_calculation_refs(ctx.calculation_packet),
            evidence_refs=["contract_evidence", "cap_evidence"],
            actionable_now=False,
            recommendation_complete=True,
            reduced_mode=ctx.calculation_packet.reduced_mode,
            confidence=_confidence(ctx),
        )
    if not ctx.calculation_packet.required_calculations_complete:
        return _insufficient(ctx, DecisionType.ROSTER_MOVE_DECISION.value, "Roster move requires completed cap/roster calculations.", ["calculation_packet"])
    action = DecisionAction.RETAIN.value if ctx.owner_objective.request_goal not in {Goal.REDUCE_SALARY.value} else DecisionAction.RELEASE.value
    return DecisionOutput(DecisionType.ROSTER_MOVE_DECISION.value, action, RecommendationStatus.RECOMMENDED_WITH_CONDITIONS.value if ctx.rules_evaluation.overall_status == "conditionally_legal" else RecommendationStatus.RECOMMENDED.value, _option("roster_move", action.title(), action, 1, None, "Roster move decision is gated by eligibility, cap, roster count, and owner objective.", calculation_refs=_calculation_types(ctx.calculation_packet), evidence_refs=["evidence_packet"], confidence=_confidence(ctx)), rule_constraints=_rule_constraints(ctx.rules_evaluation), supporting_calculations=_calculation_refs(ctx.calculation_packet), conditions=_conditions_from_rules(ctx.rules_evaluation), actionable_now=False, recommendation_complete=True, reduced_mode=ctx.calculation_packet.reduced_mode or ctx.rules_evaluation.reduced_mode, confidence=_confidence(ctx))


def _long_term_decision(ctx: _DecisionContext) -> DecisionOutput:
    base = _roster_strategy_decision(ctx)
    priorities = [
        _option("priority_cap", "Resolve cap exposure", DecisionAction.PRESERVE_FLEXIBILITY.value, 1, None, "Use verified salary commitments and cap flexibility first."),
        _option("priority_picks", "Preserve premium picks", DecisionAction.INCREASE_FUTURE_ASSETS.value, 2, None, "Draft capital supports longer-term flexibility."),
        _option("priority_depth", "Address position needs", DecisionAction.INCREASE_CURRENT_STRENGTH.value, 3, None, "Position needs come from stored team-brain evidence."),
    ][:MAX_ALTERNATIVES]
    return DecisionOutput(DecisionType.LONG_TERM_PLAN.value, base.action, base.recommendation_status, base.primary_recommendation, priorities, reasoning_factors=base.reasoning_factors, supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=base.evidence_refs, risks=base.risks, actionable_now=False, recommendation_complete=base.recommendation_complete, reduced_mode=True, confidence=base.confidence)


def _team_comparison_decision(ctx: _DecisionContext) -> DecisionOutput:
    return _comparison_summary(ctx, DecisionType.TEAM_COMPARISON.value)


def _league_analysis_decision(ctx: _DecisionContext) -> DecisionOutput:
    return _comparison_summary(ctx, DecisionType.LEAGUE_ANALYSIS.value)


def _comparison_summary(ctx: _DecisionContext, decision_type: str) -> DecisionOutput:
    teams = ctx.evidence_packet.team_evidence[:MAX_CANDIDATES]
    if not teams:
        return _insufficient(ctx, decision_type, "Team evidence is missing.", ["team_evidence"])
    options = []
    for index, team in enumerate(teams[:MAX_ALTERNATIVES]):
        score = _safe_float(team.team_brain_summary.get("championship_window_score")) if isinstance(team.team_brain_summary, dict) else None
        options.append(_option(f"team_{team.league_team_id}", team.team_name or team.league_team_id, DecisionAction.NO_RECOMMENDATION.value, index + 1, score, "Team summary uses stored team-brain fields.", related=[team.league_team_id], evidence_refs=["team_evidence"], confidence="medium"))
    return DecisionOutput(decision_type, DecisionAction.NO_RECOMMENDATION.value, RecommendationStatus.NOT_APPLICABLE.value, None, options, reasoning_factors=[_factor("summary_request", "neutral", "medium", "League/team analysis is summarized without a transaction recommendation.", ["team_evidence"])], evidence_refs=["team_evidence"], actionable_now=False, recommendation_complete=True, reduced_mode=True, confidence="medium")


def _general_decision(ctx: _DecisionContext) -> DecisionOutput:
    return DecisionOutput(DecisionType.GENERAL_CONVERSATION.value, DecisionAction.NOT_APPLICABLE.value, RecommendationStatus.NOT_APPLICABLE.value, reasoning_factors=[_factor("conversation", "neutral", "low", "General conversation does not require a structured recommendation.", ["decision_plan"])], actionable_now=False, recommendation_complete=True, confidence="high")


def _unsupported_decision(ctx: _DecisionContext) -> DecisionOutput:
    return DecisionOutput(DecisionType.UNSUPPORTED.value, DecisionAction.UNSUPPORTED.value, RecommendationStatus.UNSUPPORTED.value, unresolved_questions=[DecisionUnresolvedItem("unsupported_engine", "No authorized deterministic decision engine supports this request.", True, [ctx.decision_plan.decision_engine])], actionable_now=False, recommendation_complete=True, reduced_mode=True, confidence="unavailable")


def _validate_alignment(
    context: AssistantRequestContext,
    conversation_state: ConversationState | None,
    plan: DecisionPlan,
    evidence: EvidencePacket,
    rules: RulesEvaluation,
    calculations: CalculationPacket,
) -> None:
    if not context.user_id or not context.league_id or not context.league_team_id:
        raise ValueError("Decision execution requires authenticated user, league, and team scope.")
    if conversation_state:
        if conversation_state.user_id != context.user_id:
            raise ValueError("Conversation state user scope does not match request context.")
        if conversation_state.league_id != context.league_id:
            raise ValueError("Conversation state league scope does not match request context.")
        if conversation_state.league_team_id != context.league_team_id:
            raise ValueError("Conversation state team scope does not match request context.")
        if context.conversation_id and conversation_state.conversation_id != context.conversation_id:
            raise ValueError("Conversation state conversation id does not match request context.")
    ref = evidence.request_context_ref
    if ref.user_id != context.user_id:
        raise ValueError("Evidence user scope does not match request context.")
    if ref.league_id != context.league_id:
        raise ValueError("Evidence league scope does not match request context.")
    if ref.league_team_id != context.league_team_id:
        raise ValueError("Evidence team scope does not match request context.")
    if context.conversation_id and ref.conversation_id and ref.conversation_id != context.conversation_id:
        raise ValueError("Evidence conversation scope does not match request context.")
    if evidence.plan_type != plan.plan_type or evidence.decision_engine != plan.decision_engine:
        raise ValueError("Evidence metadata does not match decision plan.")
    if rules.evaluation_type != plan.plan_type:
        raise ValueError("Rules evaluation metadata does not match decision plan.")
    if calculations.plan_type != plan.plan_type or calculations.decision_engine != plan.decision_engine:
        raise ValueError("Calculation packet metadata does not match decision plan.")
    allowed_teams = _allowed_team_ids(context, plan)
    for team in evidence.team_evidence:
        if team.league_team_id not in allowed_teams and not _is_transaction_plan(plan):
            raise ValueError("Team evidence crossed requested scope.")


def _finalize(output: DecisionOutput, ctx: _DecisionContext) -> DecisionOutput:
    reduced = output.reduced_mode or ctx.evidence_packet.reduced_mode or ctx.rules_evaluation.reduced_mode or ctx.calculation_packet.reduced_mode
    confidence = output.confidence
    if ctx.calculation_packet.confidence == "low" or ctx.rules_evaluation.confidence == "low":
        confidence = "low"
    elif reduced and confidence == "high":
        confidence = "medium"
    complete = output.recommendation_complete and not any(item.blocking for item in output.unresolved_questions)
    actionable = output.actionable_now and complete and output.recommendation_status in {RecommendationStatus.RECOMMENDED.value, RecommendationStatus.RECOMMENDED_WITH_CONDITIONS.value}
    if ctx.rules_evaluation.overall_status in {"illegal", "unverifiable", "conditionally_legal"}:
        actionable = actionable and ctx.rules_evaluation.overall_status == "legal"
    return DecisionOutput(
        decision_type=output.decision_type,
        action=output.action,
        recommendation_status=output.recommendation_status,
        primary_recommendation=output.primary_recommendation,
        alternatives=output.alternatives[:MAX_ALTERNATIVES],
        rejected_options=output.rejected_options[:MAX_REJECTED],
        reasoning_factors=output.reasoning_factors[:MAX_FACTORS],
        rule_constraints=output.rule_constraints or _rule_constraints(ctx.rules_evaluation),
        supporting_calculations=output.supporting_calculations or _calculation_refs(ctx.calculation_packet),
        evidence_refs=_dedupe_strings(output.evidence_refs),
        risks=output.risks[:MAX_FACTORS],
        tradeoffs=output.tradeoffs[:MAX_FACTORS],
        conditions=output.conditions or _conditions_from_rules(ctx.rules_evaluation),
        unresolved_questions=output.unresolved_questions,
        actionable_now=actionable,
        recommendation_complete=complete,
        reduced_mode=reduced,
        confidence=confidence,
    )


def _rule_or_calculation_gate(ctx: _DecisionContext, *, allow_reduced: bool = False) -> DecisionOutput | None:
    return _illegal_gate(ctx, _decision_type_for_plan(ctx.decision_plan)) or _calculation_gate(ctx, _decision_type_for_plan(ctx.decision_plan), allow_reduced=allow_reduced)


def _illegal_gate(ctx: _DecisionContext, decision_type: str) -> DecisionOutput | None:
    if ctx.rules_evaluation.overall_status != "illegal":
        return None
    rejected = [RejectedOption("requested_action", "Requested action", "Confirmed rule violation prevents recommending execution.", [], "rules_evaluation")]
    return DecisionOutput(decision_type, DecisionAction.REJECT.value, RecommendationStatus.NOT_RECOMMENDED.value, _option("reject_illegal", "Reject", DecisionAction.REJECT.value, 1, None, "Confirmed league-rule violation gates the decision.", rule_status="illegal", confidence="high"), rejected_options=rejected, reasoning_factors=[_factor("rules_gating", "opposes", "critical", "Confirmed illegal result overrides strategic desirability.", ["rules_evaluation"])], rule_constraints=_rule_constraints(ctx.rules_evaluation), actionable_now=False, recommendation_complete=True, reduced_mode=ctx.rules_evaluation.reduced_mode, confidence="high")


def _calculation_gate(ctx: _DecisionContext, decision_type: str, *, allow_reduced: bool = False, missing_message: str = "Required calculations are incomplete.") -> DecisionOutput | None:
    if ctx.calculation_packet.required_calculations_complete:
        return None
    if allow_reduced and not any(item.blocking for item in ctx.calculation_packet.unresolved_calculations):
        return None
    return DecisionOutput(decision_type, DecisionAction.REQUEST_MORE_INFORMATION.value, RecommendationStatus.INSUFFICIENT_INFORMATION.value, unresolved_questions=[DecisionUnresolvedItem("calculation_incomplete", missing_message, True, [item.calculation_type for item in ctx.calculation_packet.unresolved_calculations])], supporting_calculations=_calculation_refs(ctx.calculation_packet), actionable_now=False, recommendation_complete=True, reduced_mode=True, confidence="low")


def _hard_constraint_gate(ctx: _DecisionContext, decision_type: str) -> DecisionOutput | None:
    rejected = []
    for constraint in ctx.owner_objective.non_negotiables:
        rejected.extend(_constraint_rejections(ctx, constraint))
    if not rejected:
        return None
    return DecisionOutput(decision_type, DecisionAction.REJECT.value, RecommendationStatus.NOT_RECOMMENDED.value, _option("reject_constraint", "Reject", DecisionAction.REJECT.value, 1, None, "A hard owner constraint blocks the requested option.", confidence="high"), rejected_options=rejected[:MAX_REJECTED], reasoning_factors=[_factor("hard_constraint", "opposes", "critical", "Hard owner constraints are enforced before scoring.", ["owner_objective.non_negotiables"])], actionable_now=False, recommendation_complete=True, reduced_mode=False, confidence="high")


def _constraint_rejections(ctx: _DecisionContext, constraint: ObjectiveConstraint) -> list[RejectedOption]:
    key = str(constraint.constraint_type or "").lower()
    if not constraint.hard:
        return []
    assets = ctx.interpreted_question.included_assets
    if key in {"do_not_trade_first_round_pick", "preserve_first_round_pick"}:
        for asset in assets:
            label = str(asset.label or asset.canonical_id or "").lower()
            if asset.asset_type == "draft_pick" and ("1." in label or "first" in label or asset.season):
                return [RejectedOption("first_round_pick", "First-round pick", "Owner hard constraint excludes first-round picks.", [asset.canonical_id or asset.label or ""], None)]
    if key in {"do_not_trade_player", "excluded_player"}:
        blocked = str(constraint.value or "")
        return [
            RejectedOption(asset.canonical_id or asset.label or "excluded_player", asset.label or "Excluded player", "Owner hard constraint excludes this player.", [asset.canonical_id or ""], None)
            for asset in assets
            if blocked and (blocked == asset.canonical_id or blocked.lower() in str(asset.label or "").lower())
        ]
    return []


def _insufficient(ctx: _DecisionContext, decision_type: str, explanation: str, missing: list[str], *, action: str = DecisionAction.REQUEST_MORE_INFORMATION.value) -> DecisionOutput:
    return DecisionOutput(decision_type, action, RecommendationStatus.INSUFFICIENT_INFORMATION.value, unresolved_questions=[DecisionUnresolvedItem("missing_required_information", explanation, True, missing)], supporting_calculations=_calculation_refs(ctx.calculation_packet), evidence_refs=["evidence_packet"], actionable_now=False, recommendation_complete=True, reduced_mode=True, confidence="low")


def _blocked_output(decision_type: str, action: str, explanation: str, missing_refs: list[str]) -> DecisionOutput:
    return DecisionOutput(decision_type, action, RecommendationStatus.BLOCKED.value, unresolved_questions=[DecisionUnresolvedItem("scope_or_upstream_blocker", explanation, True, missing_refs)], actionable_now=False, recommendation_complete=False, reduced_mode=True, confidence="low")


def _option(option_id: str, label: str, action: str, rank: int | None, score: float | None, explanation: str, *, related: list[str] | None = None, objective_fit: dict[str, Any] | None = None, strengths: list[str] | None = None, weaknesses: list[str] | None = None, rule_status: str | None = None, calculation_refs: list[str] | None = None, evidence_refs: list[str] | None = None, confidence: str = "medium") -> RecommendationOption:
    return RecommendationOption(option_id, label, action, rank, score, "0-100 objective-fit score" if score is not None else None, explanation, _dedupe_strings(related or []), objective_fit or {}, strengths or [], weaknesses or [], rule_status, _dedupe_strings(calculation_refs or []), _dedupe_strings(evidence_refs or []), confidence)


def _factor(factor_type: str, direction: str, importance: str, explanation: str, refs: list[str]) -> DecisionFactor:
    return DecisionFactor(factor_type, direction, importance, explanation, refs)


def _rule_constraints(rules: RulesEvaluation) -> list[DecisionRuleConstraint]:
    out = []
    for violation in rules.violations:
        out.append(DecisionRuleConstraint(violation.rule_type, "violated", violation.explanation, violation.blocking, ["rules_evaluation.violations"]))
    for condition in rules.conditions:
        out.append(DecisionRuleConstraint(condition.condition_type, "conditional", condition.explanation, condition.blocking_if_unsatisfied, ["rules_evaluation.conditions"]))
    for unresolved in rules.unresolved_rules:
        out.append(DecisionRuleConstraint(unresolved.rule_type, "unresolved", unresolved.explanation, unresolved.blocking, ["rules_evaluation.unresolved_rules"]))
    return out


def _conditions_from_rules(rules: RulesEvaluation) -> list[DecisionCondition]:
    return [DecisionCondition(item.condition_type, item.explanation, item.satisfied, item.blocking_if_unsatisfied, item.required_evidence) for item in rules.conditions]


def _unresolved_from_rules(rules: RulesEvaluation) -> list[DecisionUnresolvedItem]:
    return [DecisionUnresolvedItem(item.rule_type, item.explanation, item.blocking, item.missing_evidence) for item in rules.unresolved_rules]


def _calculation_refs(packet: CalculationPacket) -> list[DecisionCalculationRef]:
    refs = []
    for result in packet.results:
        refs.append(DecisionCalculationRef(result.calculation_type, result.status, result.output_key, _compact(result.value), result.exact, result.estimated))
    return refs


def _calculation_types(packet: CalculationPacket) -> list[str]:
    return _dedupe_strings([result.calculation_type for result in packet.results])


def _result_value(packet: CalculationPacket, calculation_type: str) -> Any:
    for result in packet.results:
        if result.calculation_type == calculation_type or result.output_key == calculation_type:
            return result.value
    return None


def _numeric_result(packet: CalculationPacket, calculation_type: str) -> float | None:
    return _safe_float(_result_value(packet, calculation_type))


def _has_unresolved(packet: CalculationPacket, calculation_type: str) -> bool:
    return any(item.calculation_type == calculation_type for item in packet.unresolved_calculations)


def _dict_number(value: Any, key: str) -> float | None:
    if isinstance(value, dict):
        return _safe_float(value.get(key))
    return None


def _objective_fit_score(ctx: _DecisionContext, *, value: float | None, age: float | None, cap_delta: float | None) -> tuple[float | None, dict[str, Any]]:
    weights = _weights(ctx.owner_objective)
    dimensions: dict[str, float] = {}
    if value is not None:
        dimensions["value"] = _bounded_score(value)
    if age is not None:
        dimensions["age"] = 80 if ctx.owner_objective.request_goal in {Goal.GET_YOUNGER.value, Goal.REBUILD.value, Goal.RETOOL.value} and age <= 25 else 55
    if cap_delta is not None:
        dimensions["cap"] = 80 if cap_delta >= 0 else 35
    if not dimensions:
        return None, {"coverage": 0, "weights": weights}
    total_weight = sum(weights.get(key, 0) for key in dimensions) or len(dimensions)
    score = sum(dimensions[key] * (weights.get(key, 1) if total_weight != len(dimensions) else 1) for key in dimensions) / total_weight
    return round(score, 2), {"dimensions": dimensions, "weights": weights, "coverage": round(len(dimensions) / max(len(weights), 1), 2), "request_goal": ctx.owner_objective.request_goal}


def _weights(objective: OwnerObjective) -> dict[str, float]:
    goal = objective.request_goal
    if goal in {Goal.WIN_NOW.value, Goal.CONTEND_THIS_SEASON.value, Goal.IMPROVE_CURRENT_ROSTER.value}:
        return {"value": 0.55, "age": 0.15, "cap": 0.30}
    if goal in {Goal.REBUILD.value, Goal.GET_YOUNGER.value, Goal.INCREASE_FUTURE_VALUE.value, Goal.RETOOL.value}:
        return {"value": 0.35, "age": 0.45, "cap": 0.20}
    if goal in {Goal.REDUCE_SALARY.value, Goal.PRESERVE_CAP_FLEXIBILITY.value}:
        return {"value": 0.25, "age": 0.15, "cap": 0.60}
    return {"value": 0.50, "age": 0.25, "cap": 0.25}


def _objective_factors(ctx: _DecisionContext, fit: dict[str, Any]) -> list[DecisionFactor]:
    return [_factor("objective_fit", "supports", "high", f"Objective fit used request goal {ctx.owner_objective.request_goal} with coverage {fit.get('coverage')}.", ["owner_objective", "calculation_packet"])]


def _player_value(player: PlayerEvidence | None) -> float | None:
    if not player:
        return None
    for source in (player.league_relative_value, player.strategic_profile):
        if not isinstance(source, dict):
            continue
        for key in ("overall_value_score", "value_score", "asset_score", "dynasty_score", "win_now_score"):
            value = _safe_float(source.get(key))
            if value is not None:
                return round(value, 2)
    return None


def _poor_contract(efficiency: Any, player_id: str) -> bool:
    if not isinstance(efficiency, dict):
        return False
    value = _safe_float(efficiency.get(player_id))
    return value is not None and value < 2


def _risks_from_player(player: PlayerEvidence) -> list[DecisionRisk]:
    risks = []
    risk_text = player.strategic_profile.get("risk") if isinstance(player.strategic_profile, dict) else None
    if risk_text:
        risks.append(DecisionRisk("stored_player_risk", "medium", str(risk_text), [player.player_id], ["player_evidence.strategic_profile"]))
    return risks


def _tradeoffs_from_value(value_delta: float) -> list[DecisionTradeoff]:
    if value_delta > 0:
        return [DecisionTradeoff("outgoing package", "higher verified incoming value", "high", "Positive value delta supports the incoming side on the verified scale.")]
    if value_delta < 0:
        return [DecisionTradeoff("higher verified outgoing value", "incoming package", "high", "Negative value delta means the outgoing side is stronger on the verified scale.")]
    return [DecisionTradeoff("similar verified value", "similar verified value", "medium", "Verified values are close enough that non-value factors matter.")]


def _team(ctx: _DecisionContext) -> TeamEvidence | None:
    for team in ctx.evidence_packet.team_evidence:
        if team.league_team_id == ctx.context.league_team_id:
            return team
    return ctx.evidence_packet.team_evidence[0] if ctx.evidence_packet.team_evidence else None


def _confidence(ctx: _DecisionContext) -> str:
    if not ctx.evidence_packet.required_evidence_complete or not ctx.calculation_packet.required_calculations_complete:
        return "low"
    if ctx.rules_evaluation.confidence == "low" or ctx.calculation_packet.confidence == "low":
        return "low"
    if any(result.estimated for result in ctx.calculation_packet.results):
        return "medium"
    if ctx.evidence_packet.reduced_mode or ctx.rules_evaluation.reduced_mode or ctx.calculation_packet.reduced_mode:
        return "medium"
    if ctx.owner_objective.confidence == "low":
        return "medium"
    return "high"


def _decision_type_for_plan(plan: DecisionPlan) -> str:
    mapping = {
        "factual_lookup_plan": DecisionType.FACTUAL_RESPONSE.value,
        "rules_lookup_plan": DecisionType.RULES_RESPONSE.value,
        "player_evaluation_plan": DecisionType.PLAYER_EVALUATION.value,
        "player_comparison_plan": DecisionType.PLAYER_COMPARISON.value,
        "roster_evaluation_plan": DecisionType.ROSTER_STRATEGY.value,
        "trade_evaluation_plan": DecisionType.TRADE_EVALUATION.value,
        "trade_discovery_plan": DecisionType.TRADE_DISCOVERY.value,
        "trade_construction_plan": DecisionType.TRADE_CONSTRUCTION.value,
        "draft_recommendation_plan": DecisionType.DRAFT_RECOMMENDATION.value,
        "draft_pick_evaluation_plan": DecisionType.DRAFT_PICK_EVALUATION.value,
        "free_agent_plan": DecisionType.FREE_AGENT_RECOMMENDATION.value,
        "contract_plan": DecisionType.CONTRACT_DECISION.value,
        "salary_cap_plan": DecisionType.SALARY_CAP_STRATEGY.value,
        "lineup_plan": DecisionType.LINEUP_DECISION.value,
        "roster_move_plan": DecisionType.ROSTER_MOVE_DECISION.value,
        "long_term_planning_plan": DecisionType.LONG_TERM_PLAN.value,
        "team_comparison_plan": DecisionType.TEAM_COMPARISON.value,
        "league_analysis_plan": DecisionType.LEAGUE_ANALYSIS.value,
        "scenario_simulation_plan": DecisionType.FACTUAL_RESPONSE.value,
        "general_conversation_plan": DecisionType.GENERAL_CONVERSATION.value,
        "unsupported_plan": DecisionType.UNSUPPORTED.value,
    }
    return mapping.get(plan.plan_type, DecisionType.NO_DECISION.value)


def _allowed_team_ids(context: AssistantRequestContext, plan: DecisionPlan) -> set[str]:
    ids = {context.league_team_id}
    for request in plan.retrieval_requests:
        ids.update(_dedupe_strings(request.team_ids))
    return ids


def _is_transaction_plan(plan: DecisionPlan) -> bool:
    return plan.plan_type in {"trade_evaluation_plan", "trade_construction_plan", "roster_move_plan", "free_agent_plan"}


def _safe_float(value: Any) -> float | None:
    try:
        text = str(value).strip()
        if text.lower() in {"", "none", "null", "nan"}:
            return None
        return float(text)
    except Exception:
        return None


def _bounded_score(value: Any) -> float:
    number = _safe_float(value) or 0.0
    return round(max(0.0, min(100.0, number)), 2)


def _dedupe_strings(items: list[str | None]) -> list[str]:
    out = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _compact(inner)
            for key, inner in value.items()
            if inner not in (None, "", [], {}) and key not in {"raw", "raw_row", "exception", "traceback", "service_key", "access_token", "refresh_token", "scratchpad", "chain_of_thought"}
        }
    if isinstance(value, list):
        return [_compact(item) for item in value if item not in (None, "", [], {})]
    return value


DECISION_ENGINES: dict[str, DecisionHandler] = {
    "factual_lookup_engine": _factual_decision,
    "rules_explanation_engine": _rules_decision,
    "player_evaluation_engine": _player_decision,
    "player_comparison_engine": _comparison_decision,
    "roster_evaluation_engine": _roster_strategy_decision,
    "trade_evaluation_engine": _trade_evaluation_decision,
    "trade_discovery_engine": _trade_discovery_decision,
    "trade_construction_engine": _trade_construction_decision,
    "draft_recommendation_engine": _draft_recommendation_decision,
    "draft_pick_evaluation_engine": _draft_pick_decision,
    "free_agent_engine": _free_agent_decision,
    "contract_engine": _contract_decision,
    "salary_cap_engine": _salary_cap_decision,
    "lineup_engine": _lineup_decision,
    "roster_move_engine": _roster_move_decision,
    "long_term_planning_engine": _long_term_decision,
    "team_comparison_engine": _team_comparison_decision,
    "league_analysis_engine": _league_analysis_decision,
    "scenario_simulation_engine": _factual_decision,
    "conversation_engine": _general_decision,
    "unsupported_engine": _unsupported_decision,
}
