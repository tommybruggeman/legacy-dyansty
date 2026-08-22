from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable

from gm_assistant.calculations import CalculationPacket
from gm_assistant.conversation_state import ConversationState
from gm_assistant.decision import (
    DecisionAction,
    DecisionOutput,
    DecisionType,
    RecommendationOption,
    RecommendationStatus,
)
from gm_assistant.evidence import EvidencePacket
from gm_assistant.interpretation import InterpretedQuestion
from gm_assistant.objective import OwnerObjective
from gm_assistant.planning import DecisionPlan
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.rules import RulesEvaluation


VALIDATION_VERSION = "gm_recommendation_validation.v1"
VALIDATION_ENGINE_VERSION = "deterministic_validation_stage10.v1"


class ValidationStatus(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_WARNINGS = "approved_with_warnings"
    APPROVED_WITH_CONDITIONS = "approved_with_conditions"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class ValidationCheckStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class ValidationCheck:
    check_type: str
    status: str
    explanation: str
    blocking: bool = False
    source_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationIssue:
    issue_type: str
    severity: str
    explanation: str
    source_refs: list[str] = field(default_factory=list)
    blocking: bool = False


@dataclass(frozen=True)
class ValidationContradiction:
    contradiction_type: str
    explanation: str
    source_refs: list[str] = field(default_factory=list)
    blocking: bool = True


@dataclass(frozen=True)
class MissingDecisionSupport:
    support_type: str
    explanation: str
    required: bool
    missing_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidatedCondition:
    condition_type: str
    explanation: str
    satisfied: bool | None
    blocking: bool
    source_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidatedReference:
    reference_type: str
    reference_id: str
    validated: bool
    source_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RecommendationValidation:
    validation_status: str
    approved_for_explanation: bool
    approved_for_action: bool
    checks: list[ValidationCheck] = field(default_factory=list)
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    contradictions: list[ValidationContradiction] = field(default_factory=list)
    missing_support: list[MissingDecisionSupport] = field(default_factory=list)
    conditions: list[ValidatedCondition] = field(default_factory=list)
    validated_references: list[ValidatedReference] = field(default_factory=list)
    decision_action: str | None = None
    recommendation_status: str | None = None
    confidence_after_validation: str = "unavailable"
    validation_complete: bool = False
    reduced_mode: bool = False
    validation_version: str = VALIDATION_VERSION
    validation_engine_version: str = VALIDATION_ENGINE_VERSION

    def to_packet(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _ValidationWork:
    checks: list[ValidationCheck] = field(default_factory=list)
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    contradictions: list[ValidationContradiction] = field(default_factory=list)
    missing_support: list[MissingDecisionSupport] = field(default_factory=list)
    conditions: list[ValidatedCondition] = field(default_factory=list)
    refs: list[ValidatedReference] = field(default_factory=list)

    def check(self, check_type: str, status: str, explanation: str, *, blocking: bool = False, refs: list[str] | None = None) -> None:
        self.checks.append(ValidationCheck(check_type, status, explanation, blocking, _dedupe(refs or [])))

    def error(self, issue_type: str, explanation: str, *, blocking: bool = True, refs: list[str] | None = None) -> None:
        self.errors.append(ValidationIssue(issue_type, ValidationSeverity.BLOCKING.value if blocking else ValidationSeverity.ERROR.value, explanation, _dedupe(refs or []), blocking))

    def warn(self, issue_type: str, explanation: str, *, refs: list[str] | None = None) -> None:
        self.warnings.append(ValidationIssue(issue_type, ValidationSeverity.WARNING.value, explanation, _dedupe(refs or []), False))

    def contradiction(self, contradiction_type: str, explanation: str, *, refs: list[str] | None = None) -> None:
        self.contradictions.append(ValidationContradiction(contradiction_type, explanation, _dedupe(refs or []), True))

    def missing(self, support_type: str, explanation: str, *, required: bool, refs: list[str] | None = None) -> None:
        self.missing_support.append(MissingDecisionSupport(support_type, explanation, required, _dedupe(refs or [])))


@dataclass(frozen=True)
class _ValidationContext:
    context: AssistantRequestContext
    conversation_state: ConversationState | None
    interpreted_question: InterpretedQuestion
    owner_objective: OwnerObjective
    decision_plan: DecisionPlan
    evidence_packet: EvidencePacket
    rules_evaluation: RulesEvaluation
    calculation_packet: CalculationPacket
    decision_output: DecisionOutput


ValidationHandler = Callable[[_ValidationContext, _ValidationWork], None]


PLAN_TO_DECISION = {
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

ENGINE_ACTIONS = {
    DecisionType.TRADE_EVALUATION.value: {"accept", "reject", "counter", "hold", "request_more_information"},
    DecisionType.TRADE_CONSTRUCTION.value: {"counter", "hold", "reject", "request_more_information"},
    DecisionType.LINEUP_DECISION.value: {"start", "bench", "flex", "no_clear_preference", "request_more_information", "reject"},
    DecisionType.CONTRACT_DECISION.value: {"extend", "do_not_extend", "release", "restructure", "wait", "request_terms", "request_more_information", "retain", "reject"},
    DecisionType.DRAFT_RECOMMENDATION.value: {"draft_player", "trade_down", "trade_up", "hold_pick", "best_player_available", "request_more_information"},
    DecisionType.DRAFT_PICK_EVALUATION.value: {"hold_pick", "sell_pick", "trade_down", "trade_up", "wait", "request_more_information"},
    DecisionType.FREE_AGENT_RECOMMENDATION.value: {"acquire", "do_not_pursue", "pursue", "monitor", "avoid", "request_more_information", "reject"},
    DecisionType.ROSTER_MOVE_DECISION.value: {"release", "retain", "hold", "request_more_information", "reject"},
    DecisionType.PLAYER_EVALUATION.value: {"retain", "sell", "avoid", "monitor", "acquire", "hold", "request_more_information"},
    DecisionType.PLAYER_COMPARISON.value: {"prefer_player_a", "prefer_player_b", "no_clear_preference", "request_more_information"},
    DecisionType.ROSTER_STRATEGY.value: {"contend", "rebuild", "retool", "stay_balanced", "preserve_flexibility", "increase_future_assets", "increase_current_strength"},
    DecisionType.SALARY_CAP_STRATEGY.value: {"preserve_flexibility", "increase_current_strength", "request_more_information"},
    DecisionType.LONG_TERM_PLAN.value: {"contend", "rebuild", "retool", "stay_balanced", "preserve_flexibility", "increase_future_assets", "increase_current_strength"},
    DecisionType.FACTUAL_RESPONSE.value: {"not_applicable", "no_recommendation"},
    DecisionType.RULES_RESPONSE.value: {"reject", "no_recommendation", "not_applicable", "request_more_information"},
    DecisionType.TEAM_COMPARISON.value: {"no_recommendation", "not_applicable"},
    DecisionType.LEAGUE_ANALYSIS.value: {"no_recommendation", "not_applicable"},
    DecisionType.GENERAL_CONVERSATION.value: {"not_applicable", "no_recommendation"},
    DecisionType.UNSUPPORTED.value: {"unsupported", "request_more_information"},
    DecisionType.NO_DECISION.value: {"request_more_information", "unsupported", "not_applicable"},
}

EXECUTION_ACTIONS = {
    DecisionAction.ACCEPT.value,
    DecisionAction.ACQUIRE.value,
    DecisionAction.DRAFT_PLAYER.value,
    DecisionAction.EXTEND.value,
    DecisionAction.RELEASE.value,
    DecisionAction.START.value,
    DecisionAction.FLEX.value,
    DecisionAction.RESTRUCTURE.value,
}

NON_APPLICABLE_TYPES = {
    DecisionType.FACTUAL_RESPONSE.value,
    DecisionType.GENERAL_CONVERSATION.value,
    DecisionType.TEAM_COMPARISON.value,
    DecisionType.LEAGUE_ANALYSIS.value,
}


def validate_recommendation(
    *,
    context: AssistantRequestContext,
    conversation_state: ConversationState | None,
    interpreted_question: InterpretedQuestion,
    owner_objective: OwnerObjective,
    decision_plan: DecisionPlan,
    evidence_packet: EvidencePacket,
    rules_evaluation: RulesEvaluation,
    calculation_packet: CalculationPacket,
    decision_output: DecisionOutput,
) -> RecommendationValidation:
    ctx = _ValidationContext(
        context,
        conversation_state,
        interpreted_question,
        owner_objective,
        decision_plan,
        evidence_packet,
        rules_evaluation,
        calculation_packet,
        decision_output,
    )
    work = _ValidationWork()
    try:
        for handler in VALIDATION_CHECKS:
            handler(ctx, work)
        return _final_validation(ctx, work)
    except Exception:
        work.check("validator_runtime", ValidationCheckStatus.FAIL.value, "Validation failed safely.", blocking=True, refs=["validation"])
        work.error("validator_runtime", "Validation failed safely.", blocking=True, refs=["validation"])
        return RecommendationValidation(
            validation_status=ValidationStatus.FAILED.value,
            approved_for_explanation=False,
            approved_for_action=False,
            checks=work.checks,
            errors=work.errors,
            warnings=work.warnings,
            contradictions=work.contradictions,
            missing_support=work.missing_support,
            conditions=work.conditions,
            validated_references=work.refs,
            decision_action=decision_output.action,
            recommendation_status=decision_output.recommendation_status,
            confidence_after_validation="unavailable",
            validation_complete=False,
            reduced_mode=True,
        )


def build_validation_packet(validation: RecommendationValidation | None) -> dict[str, Any]:
    if not validation:
        return {}
    packet = validation.to_packet()
    return _compact(packet)


def _validate_scope_alignment(ctx: _ValidationContext, work: _ValidationWork) -> None:
    missing = [name for name, value in (("user_id", ctx.context.user_id), ("league_id", ctx.context.league_id), ("league_team_id", ctx.context.league_team_id)) if not value]
    if missing:
        work.check("scope_alignment", ValidationCheckStatus.BLOCKED.value, "Request context is missing authenticated scope.", blocking=True, refs=missing)
        work.error("scope_alignment", "Recommendation validation requires authenticated user, league, and team scope.", refs=missing)
        return
    if ctx.conversation_state:
        for attr in ("user_id", "league_id", "league_team_id"):
            if str(getattr(ctx.conversation_state, attr, "")) != str(getattr(ctx.context, attr)):
                work.error("conversation_scope_mismatch", f"Conversation state {attr} does not match request context.", refs=["conversation_state", "request_context"])
    ref = ctx.evidence_packet.request_context_ref
    for attr in ("user_id", "league_id", "league_team_id"):
        if str(getattr(ref, attr, "")) != str(getattr(ctx.context, attr)):
            work.error("evidence_scope_mismatch", f"Evidence {attr} does not match request context.", refs=["evidence_packet", "request_context"])
    if ctx.rules_evaluation.evaluation_type != ctx.decision_plan.plan_type:
        work.error("rules_plan_mismatch", "Rules evaluation type does not match the decision plan.", refs=["rules_evaluation", "decision_plan"])
    if ctx.calculation_packet.plan_type != ctx.decision_plan.plan_type or ctx.calculation_packet.decision_engine != ctx.decision_plan.decision_engine:
        work.error("calculation_plan_mismatch", "Calculation packet metadata does not match the decision plan.", refs=["calculation_packet", "decision_plan"])
    expected = PLAN_TO_DECISION.get(ctx.decision_plan.plan_type)
    if expected and ctx.decision_output.decision_type != expected and not (
        ctx.decision_plan.response_mode == "direct_factual"
        and ctx.decision_output.decision_type == DecisionType.FACTUAL_RESPONSE.value
    ):
        work.error("decision_plan_mismatch", "Decision output type does not match the decision plan.", refs=["decision_output", "decision_plan"])
    work.check("scope_alignment", _status_for_errors(work, "scope"), "Validated request, conversation, evidence, rules, calculation, and decision scope.", blocking=False, refs=["request_context"])


def _validate_request_alignment(ctx: _ValidationContext, work: _ValidationWork) -> None:
    if ctx.decision_output.decision_type in NON_APPLICABLE_TYPES and ctx.decision_output.action in EXECUTION_ACTIONS:
        work.contradiction("request_alignment", "A factual, comparison-summary, or casual response cannot be converted into a transaction action.", refs=["decision_output"])
    if ctx.decision_plan.plan_type == "rules_lookup_plan" and ctx.decision_output.decision_type != DecisionType.RULES_RESPONSE.value:
        work.error("request_alignment", "Rules lookup request produced a non-rules decision.", refs=["decision_plan", "decision_output"])
    if ctx.decision_plan.plan_type == "factual_lookup_plan" and ctx.decision_output.primary_recommendation:
        work.warn("request_alignment", "Factual lookup includes a recommendation-shaped primary option; OpenAI must treat it as explanatory only.", refs=["decision_output"])
    work.check("request_alignment", ValidationCheckStatus.PASS.value, "Decision type is compatible with the interpreted request.", refs=["interpreted_question", "decision_plan"])


def _validate_engine_alignment(ctx: _ValidationContext, work: _ValidationWork) -> None:
    allowed = ENGINE_ACTIONS.get(ctx.decision_output.decision_type, set())
    if ctx.decision_output.action not in allowed:
        work.error("engine_action_mismatch", "Selected action is not valid for the selected decision engine.", refs=["decision_output.action", "decision_plan.decision_engine"])
    for option in _options(ctx.decision_output):
        if option.action not in allowed and option.action not in {"hold", "avoid", "monitor", "request_more_information"}:
            work.error("option_action_mismatch", f"Option {option.option_id} uses an action outside this engine.", refs=[option.option_id])
    work.check("engine_alignment", ValidationCheckStatus.PASS.value, "Selected action belongs to the selected deterministic engine.", refs=["decision_plan.decision_engine", "decision_output.action"])


def _validate_rules_consistency(ctx: _ValidationContext, work: _ValidationWork) -> None:
    status = ctx.rules_evaluation.overall_status
    if status == "illegal" and ctx.decision_output.action in EXECUTION_ACTIONS:
        work.contradiction("illegal_execution", "An illegal rules result cannot approve an execution action.", refs=["rules_evaluation", "decision_output.action"])
    if status == "illegal" and ctx.decision_output.action == DecisionAction.REJECT.value:
        work.check("rules_consistency", ValidationCheckStatus.PASS.value, "Illegal rules result is preserved as a rejection.", refs=["rules_evaluation", "decision_output"])
    if status == "conditionally_legal":
        if not _has_blocking_condition(ctx.decision_output.conditions):
            work.error("condition_not_preserved", "Conditional legality must remain visible on the decision.", refs=["rules_evaluation.conditions", "decision_output.conditions"])
        if ctx.decision_output.actionable_now:
            work.contradiction("conditional_actionable_now", "Conditionally legal recommendations cannot be actionable now.", refs=["rules_evaluation", "decision_output"])
    if status == "unverifiable" and (ctx.decision_output.actionable_now or ctx.decision_output.confidence == "high"):
        work.contradiction("unverifiable_high_confidence", "Unverifiable rules cannot support high confidence or actionable status.", refs=["rules_evaluation", "decision_output"])
    for condition in ctx.rules_evaluation.conditions:
        work.conditions.append(ValidatedCondition(condition.condition_type, condition.explanation, condition.satisfied, condition.blocking_if_unsatisfied, ["rules_evaluation.conditions"]))
    work.check("rules_consistency", ValidationCheckStatus.PASS.value, "Rules status, violations, and conditions were rechecked against the recommendation.", refs=["rules_evaluation"])


def _validate_objective_alignment(ctx: _ValidationContext, work: _ValidationWork) -> None:
    blocked = False
    related = _decision_related_ids(ctx.decision_output)
    text = _decision_text(ctx.decision_output)
    for constraint in ctx.owner_objective.non_negotiables:
        key = str(constraint.constraint_type or "").lower()
        value = str(constraint.value or "").lower()
        if not constraint.hard:
            continue
        violates = False
        if key in {"do_not_trade_first_round_pick", "preserve_first_round_pick"} and ("first" in text or "1." in text):
            violates = True
        if key in {"do_not_trade_player", "excluded_player", "protected_player", "no_release_protected_player"} and value and value in text:
            violates = True
        if key in {"do_not_trade_player", "excluded_player", "protected_player", "no_release_protected_player"} and value:
            normalized_related = " ".join(related).lower()
            if value in normalized_related or any(ref and ref.lower() in value for ref in related):
                violates = True
        if key in {"age_limit", "max_age"} and "age" in text and ctx.decision_output.action in EXECUTION_ACTIONS:
            violates = True
        if violates:
            blocked = True
            work.contradiction("hard_constraint_violation", "Decision conflicts with a hard owner constraint.", refs=["owner_objective.non_negotiables", *related])
    for conflict in ctx.owner_objective.strategic_conflicts:
        work.warn("objective_conflict", conflict.explanation, refs=["owner_objective.strategic_conflicts"])
    work.check("objective_alignment", ValidationCheckStatus.FAIL.value if blocked else ValidationCheckStatus.PASS.value, "Hard owner constraints and strategic conflicts were checked.", blocking=blocked, refs=["owner_objective"])


def _validate_evidence_references(ctx: _ValidationContext, work: _ValidationWork) -> None:
    known = _known_evidence_ids(ctx.evidence_packet)
    missing = [ref for ref in _decision_related_ids(ctx.decision_output) if ref and ref not in known and not ref.startswith(("evidence_", "calculation_", "rules_", "owner_"))]
    for ref in missing:
        work.missing("evidence_reference", f"Decision references {ref}, but that entity is not present in the evidence packet.", required=True, refs=[ref])
    if missing:
        work.error("missing_evidence_reference", "One or more material decision entities are not verified by evidence.", refs=missing)
    if ctx.evidence_packet.execution_status in {"blocked", "failed"} or not ctx.evidence_packet.required_evidence_complete:
        work.missing("required_evidence", "Required evidence is incomplete, blocked, or failed.", required=True, refs=["evidence_packet"])
        if ctx.decision_output.action in EXECUTION_ACTIONS or ctx.decision_output.actionable_now:
            work.contradiction("action_without_complete_evidence", "Actionable recommendations require complete required evidence.", refs=["evidence_packet", "decision_output"])
    _validate_ownership_and_availability(ctx, work)
    for ref in sorted(known):
        work.refs.append(ValidatedReference("evidence_entity", ref, True, ["evidence_packet"]))
    work.check("evidence_references", ValidationCheckStatus.PASS.value, "Critical entities and evidence completeness were validated.", refs=["evidence_packet"])


def _validate_calculation_references(ctx: _ValidationContext, work: _ValidationWork) -> None:
    known = _known_calculation_refs(ctx.calculation_packet)
    for calc in ctx.decision_output.supporting_calculations:
        refs = [calc.calculation_type, calc.output_key]
        if not any(ref in known for ref in refs):
            work.missing("calculation_reference", f"Decision calculation {calc.calculation_type} is not present in the calculation packet.", required=True, refs=refs)
    for option in _options(ctx.decision_output):
        missing = [ref for ref in option.calculation_refs if ref not in known and not ref.startswith("calculation_packet")]
        if missing:
            work.missing("option_calculation_reference", f"Option {option.option_id} references missing calculations.", required=True, refs=missing)
    if not ctx.calculation_packet.required_calculations_complete and ctx.decision_output.action in EXECUTION_ACTIONS:
        work.contradiction("action_without_complete_calculations", "Execution actions require complete required calculations.", refs=["calculation_packet", "decision_output"])
    if any(result.estimated for result in ctx.calculation_packet.results) and ctx.decision_output.confidence == "high":
        work.warn("estimated_calculation_high_confidence", "Estimated calculations cap validation confidence below high.", refs=["calculation_packet"])
    if ctx.calculation_packet.execution_status in {"blocked", "failed"}:
        work.error("calculation_blocked", "Calculation packet is blocked or failed.", refs=["calculation_packet"])
    for ref in sorted(known):
        work.refs.append(ValidatedReference("calculation", ref, True, ["calculation_packet"]))
    work.check("calculation_references", ValidationCheckStatus.PASS.value, "Calculation references, completeness, and estimates were validated.", refs=["calculation_packet"])


def _validate_factor_support(ctx: _ValidationContext, work: _ValidationWork) -> None:
    allowed = ("evidence", "player_evidence", "team_evidence", "contract_evidence", "cap_evidence", "draft_pick_evidence", "lineup_evidence", "free_agent_evidence", "rules", "rules_evaluation", "calculation", "calculation_packet", "owner_objective", "objective", "decision_plan")
    for factor in ctx.decision_output.reasoning_factors:
        if not factor.source_refs:
            if factor.importance in {"critical", "high"}:
                work.error("unsupported_material_factor", "A material decision factor has no source reference.", refs=[factor.factor_type])
            else:
                work.warn("unsupported_factor", "A non-material decision factor has no source reference.", refs=[factor.factor_type])
            continue
        unsupported = [ref for ref in factor.source_refs if not str(ref).startswith(allowed)]
        if unsupported and factor.importance in {"critical", "high"}:
            work.error("unsupported_material_factor_ref", "A material decision factor cites an unsupported source.", refs=unsupported)
        elif unsupported:
            work.warn("unsupported_factor_ref", "A non-material decision factor cites an unsupported source.", refs=unsupported)
    work.check("decision_factors", ValidationCheckStatus.PASS.value, "Decision factors were traced to approved upstream packet types.", refs=["decision_output.reasoning_factors"])


def _validate_scores(ctx: _ValidationContext, work: _ValidationWork) -> None:
    for option in _options(ctx.decision_output):
        if option.score is not None and not (0 <= float(option.score) <= 100):
            work.error("score_out_of_bounds", f"Option {option.option_id} has a score outside 0-100.", refs=[option.option_id])
        if option.score is not None and not option.score_scale:
            work.warn("missing_score_scale", f"Option {option.option_id} has a score without an explicit scale.", refs=[option.option_id])
        coverage = option.objective_fit.get("coverage") if isinstance(option.objective_fit, dict) else None
        if coverage is not None and _safe_float(coverage) is not None and float(coverage) < 0.5 and ctx.decision_output.confidence == "high":
            work.warn("low_objective_coverage", "High confidence is not supported by low objective-fit coverage.", refs=[option.option_id])
    work.check("scores", ValidationCheckStatus.PASS.value, "Scores are bounded, scaled, and checked against objective coverage.", refs=["decision_output"])


def _validate_conditions(ctx: _ValidationContext, work: _ValidationWork) -> None:
    seen = {condition.condition_type for condition in work.conditions}
    for condition in ctx.decision_output.conditions:
        if condition.condition_type not in seen:
            work.conditions.append(ValidatedCondition(condition.condition_type, condition.explanation, condition.satisfied, condition.blocking, condition.related_refs))
            seen.add(condition.condition_type)
    if any(condition.blocking and condition.satisfied is not True for condition in work.conditions) and ctx.decision_output.actionable_now:
        work.contradiction("blocking_condition_actionable", "Blocking unsatisfied conditions must keep actionable_now false.", refs=["decision_output.conditions"])
    work.check("conditions", ValidationCheckStatus.PASS.value, "Upstream and decision conditions were preserved.", refs=["rules_evaluation.conditions", "decision_output.conditions"])


def _validate_confidence(ctx: _ValidationContext, work: _ValidationWork) -> None:
    cap = _confidence_cap(ctx)
    if _confidence_rank(ctx.decision_output.confidence) > _confidence_rank(cap):
        work.warn("confidence_capped", f"Validation caps confidence at {cap}.", refs=["decision_output.confidence"])
    work.check("confidence", ValidationCheckStatus.PASS.value, "Confidence was capped by evidence, rules, calculation, and objective completeness.", refs=["decision_output.confidence"])


def _validate_actionable_status(ctx: _ValidationContext, work: _ValidationWork) -> None:
    if not ctx.decision_output.actionable_now:
        work.check("actionable_status", ValidationCheckStatus.PASS.value, "Decision is not marked actionable now.", refs=["decision_output.actionable_now"])
        return
    if ctx.rules_evaluation.overall_status not in {"legal", "not_applicable"}:
        work.contradiction("actionable_rules_status", "Actionable recommendations require legal or not-applicable rules status.", refs=["rules_evaluation"])
    if not ctx.evidence_packet.required_evidence_complete or not ctx.calculation_packet.required_calculations_complete:
        work.contradiction("actionable_incomplete_support", "Actionable recommendations require complete evidence and calculations.", refs=["evidence_packet", "calculation_packet"])
    if any(item.blocking for item in ctx.decision_output.unresolved_questions):
        work.contradiction("actionable_unresolved_question", "Actionable recommendations cannot have blocking unresolved questions.", refs=["decision_output.unresolved_questions"])
    work.check("actionable_status", ValidationCheckStatus.PASS.value, "Actionable status was rechecked against rules, support, and unresolved blockers.", refs=["decision_output.actionable_now"])


def _validate_completeness(ctx: _ValidationContext, work: _ValidationWork) -> None:
    if ctx.decision_output.recommendation_complete and any(item.blocking for item in ctx.decision_output.unresolved_questions):
        work.contradiction("complete_with_blocking_unresolved", "A complete recommendation cannot have blocking unresolved items.", refs=["decision_output"])
    if ctx.decision_output.recommendation_status in {RecommendationStatus.BLOCKED.value, RecommendationStatus.INSUFFICIENT_INFORMATION.value} and ctx.decision_output.action in EXECUTION_ACTIONS:
        work.error("execution_on_incomplete_decision", "Blocked or insufficient decisions cannot select execution actions.", refs=["decision_output"])
    work.check("completeness", ValidationCheckStatus.PASS.value, "Decision completeness and reduced-mode flags were checked.", refs=["decision_output"])


def _validate_alternatives(ctx: _ValidationContext, work: _ValidationWork) -> None:
    option_ids: set[str] = set()
    for option in _options(ctx.decision_output):
        if option.option_id in option_ids:
            work.warn("duplicate_option_id", "Duplicate option ids reduce explanation quality.", refs=[option.option_id])
        option_ids.add(option.option_id)
    rejected_ids = {item.option_id for item in ctx.decision_output.rejected_options}
    overlap = option_ids.intersection(rejected_ids)
    if overlap:
        work.error("option_both_recommended_and_rejected", "An option cannot be both recommended and rejected.", refs=sorted(overlap))
    work.check("alternatives_and_rejections", ValidationCheckStatus.PASS.value, "Alternatives and rejected options were checked for consistency.", refs=["decision_output"])


def _validate_domain_specific(ctx: _ValidationContext, work: _ValidationWork) -> None:
    dtype = ctx.decision_output.decision_type
    if dtype == DecisionType.FREE_AGENT_RECOMMENDATION.value and ctx.decision_output.action in {DecisionAction.ACQUIRE.value, DecisionAction.PURSUE.value}:
        if not any(free.availability_verified for free in ctx.evidence_packet.free_agent_evidence):
            work.error("free_agent_availability_missing", "Free-agent recommendations require verified availability.", refs=["free_agent_evidence"])
    if dtype == DecisionType.CONTRACT_DECISION.value and ctx.decision_output.action == DecisionAction.EXTEND.value:
        if not any(result.calculation_type == "extension_schedule" for result in ctx.calculation_packet.results):
            work.error("extension_terms_missing", "Extension recommendations require supplied extension terms.", refs=["calculation_packet"])
    if dtype == DecisionType.LINEUP_DECISION.value and ctx.decision_output.action in {DecisionAction.START.value, DecisionAction.FLEX.value}:
        if not ctx.evidence_packet.lineup_evidence:
            work.error("lineup_evidence_missing", "Lineup recommendations require trusted lineup evidence.", refs=["lineup_evidence"])
    if dtype == DecisionType.DRAFT_RECOMMENDATION.value and not ctx.evidence_packet.draft_pick_evidence:
        work.missing("draft_pick_evidence", "Pick ownership or slot evidence is unavailable, so the answer must stay limited.", required=False, refs=["draft_pick_evidence"])
        work.warn("draft_pick_evidence_missing", "Draft pick ownership or slot evidence is unavailable; no player recommendation may be approved.", refs=["draft_pick_evidence"])
    if dtype == DecisionType.SALARY_CAP_STRATEGY.value and not ctx.calculation_packet.results:
        work.error("cap_calculation_missing", "Salary-cap claims require supporting cap calculations.", refs=["calculation_packet"])
    work.check("domain_specific", ValidationCheckStatus.PASS.value, "Domain-specific trade, player, draft, free-agent, contract, cap, lineup, long-term, factual, and rules gates were checked.", refs=["decision_output.decision_type"])


def _final_validation(ctx: _ValidationContext, work: _ValidationWork) -> RecommendationValidation:
    has_blocking = any(item.blocking for item in work.errors) or any(item.blocking for item in work.contradictions) or any(item.required for item in work.missing_support)
    has_conditions = any(condition.blocking and condition.satisfied is not True for condition in work.conditions)
    is_not_applicable = ctx.decision_output.decision_type in NON_APPLICABLE_TYPES or ctx.decision_output.recommendation_status == RecommendationStatus.NOT_APPLICABLE.value
    if has_blocking and any(item.issue_type.endswith("scope_mismatch") or item.issue_type == "scope_alignment" for item in work.errors):
        status = ValidationStatus.BLOCKED.value
    elif has_blocking and any(item.support_type in {"required_evidence", "calculation_reference", "evidence_reference"} for item in work.missing_support):
        status = ValidationStatus.BLOCKED.value
    elif has_blocking:
        status = ValidationStatus.REJECTED.value
    elif is_not_applicable:
        status = ValidationStatus.NOT_APPLICABLE.value
    elif has_conditions:
        status = ValidationStatus.APPROVED_WITH_CONDITIONS.value
    elif work.warnings:
        status = ValidationStatus.APPROVED_WITH_WARNINGS.value
    else:
        status = ValidationStatus.APPROVED.value

    approved_for_explanation = status in {
        ValidationStatus.APPROVED.value,
        ValidationStatus.APPROVED_WITH_WARNINGS.value,
        ValidationStatus.APPROVED_WITH_CONDITIONS.value,
        ValidationStatus.NOT_APPLICABLE.value,
    }
    approved_for_action = (
        status == ValidationStatus.APPROVED.value
        and ctx.decision_output.actionable_now
        and ctx.decision_output.action in EXECUTION_ACTIONS
        and not has_conditions
    )
    confidence = _confidence_cap(ctx)
    if status in {ValidationStatus.REJECTED.value, ValidationStatus.BLOCKED.value, ValidationStatus.FAILED.value}:
        confidence = "unavailable"
    elif work.warnings or has_conditions:
        confidence = _min_confidence(confidence, "medium")
    return RecommendationValidation(
        validation_status=status,
        approved_for_explanation=approved_for_explanation,
        approved_for_action=approved_for_action,
        checks=_dedupe_checks(work.checks),
        errors=work.errors,
        warnings=work.warnings,
        contradictions=work.contradictions,
        missing_support=work.missing_support,
        conditions=_dedupe_conditions(work.conditions),
        validated_references=_dedupe_refs(work.refs),
        decision_action=ctx.decision_output.action,
        recommendation_status=ctx.decision_output.recommendation_status,
        confidence_after_validation=confidence,
        validation_complete=status not in {ValidationStatus.FAILED.value, ValidationStatus.BLOCKED.value},
        reduced_mode=ctx.decision_output.reduced_mode or ctx.evidence_packet.reduced_mode or ctx.rules_evaluation.reduced_mode or ctx.calculation_packet.reduced_mode,
    )


def _validate_ownership_and_availability(ctx: _ValidationContext, work: _ValidationWork) -> None:
    action = ctx.decision_output.action
    ids = _decision_related_ids(ctx.decision_output)
    if action in {DecisionAction.RETAIN.value, DecisionAction.SELL.value, DecisionAction.RELEASE.value, DecisionAction.START.value, DecisionAction.BENCH.value, DecisionAction.FLEX.value}:
        owned = {player.player_id for player in ctx.evidence_packet.player_evidence if player.fantasy_team_id == ctx.context.league_team_id}
        owned.update(contract.player_id for contract in ctx.evidence_packet.contract_evidence if contract.league_team_id == ctx.context.league_team_id)
        if ids and not any(ref in owned for ref in ids):
            work.error("ownership_unverified", "Owned-player actions require verified ownership by the active league team.", refs=ids)
    if action in {DecisionAction.ACQUIRE.value, DecisionAction.PURSUE.value}:
        available = {free.player_id for free in ctx.evidence_packet.free_agent_evidence if free.availability_verified}
        available.update(player.player_id for player in ctx.evidence_packet.player_evidence if player.is_free_agent is True)
        if ids and not any(ref in available for ref in ids):
            work.warn("availability_unverified", "Acquisition interest is not action-approved without verified availability.", refs=ids)


def _known_evidence_ids(packet: EvidencePacket) -> set[str]:
    known = {"evidence_packet", "player_evidence", "team_evidence", "contract_evidence", "cap_evidence", "draft_pick_evidence", "lineup_evidence", "free_agent_evidence"}
    known.update(player.player_id for player in packet.player_evidence if player.player_id)
    known.update(team.league_team_id for team in packet.team_evidence if team.league_team_id)
    known.update(contract.player_id for contract in packet.contract_evidence if contract.player_id)
    known.update(cap.league_team_id for cap in packet.cap_evidence if cap.league_team_id)
    known.update(pick.canonical_pick_id for pick in packet.draft_pick_evidence if pick.canonical_pick_id)
    known.update(pick.current_owner_team_id for pick in packet.draft_pick_evidence if pick.current_owner_team_id)
    known.update(lineup.league_team_id for lineup in packet.lineup_evidence if lineup.league_team_id)
    known.update(free.player_id for free in packet.free_agent_evidence if free.player_id)
    return {str(item) for item in known if item}


def _known_calculation_refs(packet: CalculationPacket) -> set[str]:
    known = {"calculation_packet"}
    for result in packet.results:
        known.add(result.calculation_type)
        known.add(result.output_key)
    return {str(item) for item in known if item}


def _decision_related_ids(output: DecisionOutput) -> list[str]:
    ids: list[str] = []
    ids.extend(output.evidence_refs)
    if output.primary_recommendation:
        ids.extend(output.primary_recommendation.related_entity_ids)
    for option in output.alternatives:
        ids.extend(option.related_entity_ids)
    for item in output.rejected_options:
        ids.extend(item.related_entity_ids)
    for risk in output.risks:
        ids.extend(risk.related_entity_ids)
    for condition in output.conditions:
        ids.extend(condition.related_refs)
    return _dedupe(ids)


def _decision_text(output: DecisionOutput) -> str:
    parts = [output.decision_type, output.action, output.recommendation_status]
    for option in _options(output):
        parts.extend([option.label, option.explanation])
    for factor in output.reasoning_factors:
        parts.append(factor.explanation)
    for rejected in output.rejected_options:
        parts.extend([rejected.label, rejected.rejection_reason])
    return " ".join(str(part or "").lower() for part in parts)


def _options(output: DecisionOutput) -> list[RecommendationOption]:
    options = []
    if output.primary_recommendation:
        options.append(output.primary_recommendation)
    options.extend(output.alternatives)
    return options


def _confidence_cap(ctx: _ValidationContext) -> str:
    if (
        ctx.decision_output.confidence == "unavailable"
        or ctx.evidence_packet.execution_status in {"blocked", "failed"}
        or ctx.rules_evaluation.overall_status in {"blocked", "failed", "unverifiable"}
        or ctx.calculation_packet.execution_status in {"blocked", "failed"}
        or not ctx.evidence_packet.required_evidence_complete
        or not ctx.calculation_packet.required_calculations_complete
    ):
        return "low"
    if (
        ctx.decision_output.reduced_mode
        or ctx.evidence_packet.reduced_mode
        or ctx.rules_evaluation.reduced_mode
        or ctx.calculation_packet.reduced_mode
        or any(result.estimated for result in ctx.calculation_packet.results)
        or ctx.owner_objective.confidence == "low"
    ):
        return "medium"
    return ctx.decision_output.confidence if ctx.decision_output.confidence in {"high", "medium", "low"} else "medium"


def _confidence_rank(value: str | None) -> int:
    return {"unavailable": 0, "low": 1, "medium": 2, "high": 3}.get(str(value or "").lower(), 1)


def _min_confidence(a: str, b: str) -> str:
    return a if _confidence_rank(a) <= _confidence_rank(b) else b


def _has_blocking_condition(conditions: list[Any]) -> bool:
    return any(getattr(condition, "blocking", False) and getattr(condition, "satisfied", None) is not True for condition in conditions)


def _status_for_errors(work: _ValidationWork, prefix: str) -> str:
    if any(item.issue_type.startswith(prefix) for item in work.errors):
        return ValidationCheckStatus.FAIL.value
    return ValidationCheckStatus.PASS.value


def _safe_float(value: Any) -> float | None:
    try:
        text = str(value).strip()
        if text.lower() in {"", "none", "null", "nan"}:
            return None
        return float(text)
    except Exception:
        return None


def _dedupe(items: list[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _dedupe_checks(checks: list[ValidationCheck]) -> list[ValidationCheck]:
    out: list[ValidationCheck] = []
    seen: set[tuple[str, str, str]] = set()
    for check in checks:
        key = (check.check_type, check.status, check.explanation)
        if key not in seen:
            out.append(check)
            seen.add(key)
    return out


def _dedupe_conditions(conditions: list[ValidatedCondition]) -> list[ValidatedCondition]:
    out: list[ValidatedCondition] = []
    seen: set[tuple[str, str]] = set()
    for condition in conditions:
        key = (condition.condition_type, condition.explanation)
        if key not in seen:
            out.append(condition)
            seen.add(key)
    return out


def _dedupe_refs(refs: list[ValidatedReference]) -> list[ValidatedReference]:
    out: list[ValidatedReference] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (ref.reference_type, ref.reference_id)
        if key not in seen:
            out.append(ref)
            seen.add(key)
    return out


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _compact(inner)
            for key, inner in value.items()
            if inner not in (None, "", [], {}) and key not in {"raw", "raw_row", "exception", "traceback", "sql", "service_key", "access_token", "refresh_token", "scratchpad", "chain_of_thought"}
        }
    if isinstance(value, list):
        return [_compact(item) for item in value if item not in (None, "", [], {})]
    return value


VALIDATION_CHECKS: list[ValidationHandler] = [
    _validate_scope_alignment,
    _validate_request_alignment,
    _validate_engine_alignment,
    _validate_rules_consistency,
    _validate_objective_alignment,
    _validate_evidence_references,
    _validate_calculation_references,
    _validate_factor_support,
    _validate_scores,
    _validate_conditions,
    _validate_confidence,
    _validate_actionable_status,
    _validate_completeness,
    _validate_alternatives,
    _validate_domain_specific,
]
