from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from gm_assistant.calculations import CalculationPacket, CalculationResult
from gm_assistant.conversation_state import ConversationState
from gm_assistant.decision import DecisionOutput
from gm_assistant.evidence import EvidencePacket
from gm_assistant.interpretation import InterpretedQuestion, is_football_intelligence_question, is_league_owner_intelligence_question, is_roster_list_question, is_team_identity_question
from gm_assistant.objective import OwnerObjective
from gm_assistant.planning import DecisionPlan
from gm_assistant.request_context import AssistantRequestContext
from gm_assistant.scenario_simulator import is_scenario_question
from gm_assistant.rules import RulesEvaluation
from gm_assistant.validation import RecommendationValidation, ValidationStatus


ANSWER_VERSION = "gm_answer_packet.v1"
ANSWER_ASSEMBLER_VERSION = "deterministic_answer_assembler_stage11.v1"

MAX_FACTS = 8
MAX_REASONS = 5
MAX_CALCULATIONS = 5
MAX_RULES = 5
MAX_CONDITIONS = 5
MAX_WARNINGS = 5
MAX_LIMITATIONS = 5
MAX_ALTERNATIVES = 3
MAX_FORBIDDEN = 10
MAX_SOURCES = 20
MAX_AUDIT = 20


class AnswerMode(str, Enum):
    DIRECT_FACT = "direct_fact"
    DIRECT_RULES = "direct_rules"
    RECOMMENDATION = "recommendation"
    COMPARISON = "comparison"
    RANKED_OPTIONS = "ranked_options"
    CONDITIONAL_RECOMMENDATION = "conditional_recommendation"
    LIMITED_INFORMATION = "limited_information"
    CLARIFICATION_REQUIRED = "clarification_required"
    UNSUPPORTED = "unsupported"
    GENERAL_CONVERSATION = "general_conversation"
    BLOCKED = "blocked"
    FAILED = "failed"


class ResponseStatus(str, Enum):
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    READY_WITH_CONDITIONS = "ready_with_conditions"
    LIMITED = "limited"
    CLARIFICATION_REQUIRED = "clarification_required"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True)
class AnswerClaim:
    claim_type: str
    text: str
    status: str
    source_refs: list[str]
    confidence: str


@dataclass(frozen=True)
class AnswerRecommendation:
    action: str
    label: str
    explanation: str
    actionable_now: bool
    recommendation_status: str
    related_entity_ids: list[str]
    source_refs: list[str]
    condition_refs: list[str]


@dataclass(frozen=True)
class AnswerFact:
    fact_id: str
    fact_type: str
    label: str
    value: Any
    unit: str | None
    entity_ids: list[str]
    source_refs: list[str]
    freshness_status: str | None = None


@dataclass(frozen=True)
class AnswerRuleConclusion:
    rule_type: str
    status: str
    explanation: str
    blocking: bool
    source_refs: list[str]


@dataclass(frozen=True)
class AnswerCalculation:
    calculation_type: str
    label: str
    value: Any
    unit: str | None
    exact: bool
    estimated: bool
    method: str
    source_refs: list[str]
    assumptions: list[str]


@dataclass(frozen=True)
class AnswerReason:
    reason_type: str
    importance: str
    direction: str
    explanation: str
    source_refs: list[str]


@dataclass(frozen=True)
class AnswerCondition:
    condition_id: str
    condition_type: str
    explanation: str
    satisfied: bool | None
    blocking: bool
    source_refs: list[str]


@dataclass(frozen=True)
class AnswerWarning:
    warning_type: str
    explanation: str
    severity: str
    source_refs: list[str]


@dataclass(frozen=True)
class AnswerLimitation:
    limitation_type: str
    explanation: str
    effect_on_answer: str
    source_refs: list[str]


@dataclass(frozen=True)
class AnswerAlternative:
    label: str
    action: str
    explanation: str
    rank: int | None
    source_refs: list[str]


@dataclass(frozen=True)
class ForbiddenAnswerClaim:
    claim_type: str
    explanation: str
    related_refs: list[str]


@dataclass(frozen=True)
class AnswerSourceReference:
    ref_id: str
    packet_type: str
    source_type: str
    source_label: str
    entity_ids: list[str]


@dataclass(frozen=True)
class AnswerAuditEvent:
    event_type: str
    status: str
    explanation: str
    source_refs: list[str]


@dataclass(frozen=True)
class AnswerPacket:
    answer_mode: str
    response_status: str
    direct_answer: AnswerClaim | None
    recommendation: AnswerRecommendation | None
    facts: list[AnswerFact] = field(default_factory=list)
    rule_conclusions: list[AnswerRuleConclusion] = field(default_factory=list)
    calculations: list[AnswerCalculation] = field(default_factory=list)
    reasons: list[AnswerReason] = field(default_factory=list)
    conditions: list[AnswerCondition] = field(default_factory=list)
    warnings: list[AnswerWarning] = field(default_factory=list)
    limitations: list[AnswerLimitation] = field(default_factory=list)
    alternatives: list[AnswerAlternative] = field(default_factory=list)
    forbidden_claims: list[ForbiddenAnswerClaim] = field(default_factory=list)
    source_index: list[AnswerSourceReference] = field(default_factory=list)
    audit_events: list[AnswerAuditEvent] = field(default_factory=list)
    approved_for_explanation: bool = False
    approved_for_action: bool = False
    clarification_required: bool = False
    reduced_mode: bool = False
    confidence: str = "unavailable"
    answer_version: str = ANSWER_VERSION
    assembler_version: str = ANSWER_ASSEMBLER_VERSION

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _AnswerContext:
    context: AssistantRequestContext
    conversation_state: ConversationState | None
    interpreted_question: InterpretedQuestion
    owner_objective: OwnerObjective
    decision_plan: DecisionPlan
    evidence_packet: EvidencePacket
    rules_evaluation: RulesEvaluation
    calculation_packet: CalculationPacket
    decision_output: DecisionOutput
    recommendation_validation: RecommendationValidation
    league_owner_intelligence_context: Any | None = None
    football_intelligence_context: Any | None = None


def build_answer_packet(
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
    recommendation_validation: RecommendationValidation,
    league_owner_intelligence_context: Any | None = None,
    football_intelligence_context: Any | None = None,
) -> AnswerPacket:
    ctx = _AnswerContext(
        context=context,
        conversation_state=conversation_state,
        interpreted_question=interpreted_question,
        owner_objective=owner_objective,
        decision_plan=decision_plan,
        evidence_packet=evidence_packet,
        rules_evaluation=rules_evaluation,
        calculation_packet=calculation_packet,
        decision_output=decision_output,
        recommendation_validation=recommendation_validation,
        league_owner_intelligence_context=league_owner_intelligence_context,
        football_intelligence_context=football_intelligence_context,
    )
    try:
        alignment_errors = _alignment_errors(ctx)
        audit = _audit(ctx)
        if alignment_errors:
            return _blocked_packet(ctx, alignment_errors, audit)
        mode = _answer_mode(ctx)
        status = _response_status(ctx, mode)
        direct_answer = _direct_answer(ctx, mode, status)
        facts = _facts(ctx, mode)
        rules = _rules(ctx)
        calculations = _calculations(ctx)
        reasons = _reasons(ctx)
        conditions = _conditions(ctx)
        warnings = _warnings(ctx)
        limitations = _limitations(ctx, mode)
        recommendation = _recommendation(ctx, conditions)
        alternatives = _alternatives(ctx) if recommendation else []
        forbidden = _forbidden_claims(ctx, recommendation, conditions, calculations, limitations)
        sources = _source_index(
            direct_answer,
            recommendation,
            facts,
            rules,
            calculations,
            reasons,
            conditions,
            warnings,
            limitations,
            alternatives,
            forbidden,
        )
        return AnswerPacket(
            answer_mode=mode,
            response_status=status,
            direct_answer=direct_answer,
            recommendation=recommendation,
            facts=facts[:MAX_FACTS],
            rule_conclusions=rules[:MAX_RULES],
            calculations=calculations[:MAX_CALCULATIONS],
            reasons=reasons[:MAX_REASONS],
            conditions=conditions[:MAX_CONDITIONS],
            warnings=warnings[:MAX_WARNINGS],
            limitations=limitations[:MAX_LIMITATIONS],
            alternatives=alternatives[:MAX_ALTERNATIVES],
            forbidden_claims=forbidden[:MAX_FORBIDDEN],
            source_index=sources[:MAX_SOURCES],
            audit_events=(audit + [AnswerAuditEvent("answer_assembled", status, "Answer packet assembled from approved Stage 1-10 material.", ["answer_packet"])])[:MAX_AUDIT],
            approved_for_explanation=ctx.recommendation_validation.approved_for_explanation,
            approved_for_action=bool(recommendation and ctx.recommendation_validation.approved_for_action and not any(item.blocking and item.satisfied is not True for item in conditions)),
            clarification_required=status == ResponseStatus.CLARIFICATION_REQUIRED.value,
            reduced_mode=_reduced_mode(ctx, status, warnings, limitations),
            confidence=_confidence(ctx, status, calculations, warnings, limitations),
        )
    except Exception:
        return AnswerPacket(
            answer_mode=AnswerMode.FAILED.value,
            response_status=ResponseStatus.FAILED.value,
            direct_answer=AnswerClaim("failed", "I cannot safely assemble an answer from the current assistant packets.", "failed", ["answer_packet"], "unavailable"),
            recommendation=None,
            limitations=[AnswerLimitation("assembly_failed", "Answer assembly failed safely.", "Use a safe failure response.", ["answer_packet"])],
            forbidden_claims=[ForbiddenAnswerClaim("replacement_recommendation", "Do not create a replacement recommendation after answer assembly failed.", ["answer_packet"])],
            audit_events=[AnswerAuditEvent("answer_assembly", "failed", "Answer assembly failed safely.", ["answer_packet"])],
            approved_for_explanation=False,
            approved_for_action=False,
            clarification_required=False,
            reduced_mode=True,
            confidence="unavailable",
        )


def build_answer_packet_payload(answer_packet: AnswerPacket | None) -> dict[str, Any]:
    if not answer_packet:
        return {}
    payload = answer_packet.to_payload()
    return _compact(payload)


def _alignment_errors(ctx: _AnswerContext) -> list[str]:
    errors = []
    if not ctx.context.user_id or not ctx.context.league_id or not ctx.context.league_team_id:
        errors.append("request_context missing authenticated scope")
    if ctx.conversation_state:
        for attr in ("user_id", "league_id", "league_team_id"):
            if str(getattr(ctx.conversation_state, attr, "")) != str(getattr(ctx.context, attr)):
                errors.append(f"conversation_state {attr} mismatch")
        if ctx.context.conversation_id and ctx.conversation_state.conversation_id != ctx.context.conversation_id:
            errors.append("conversation_state conversation_id mismatch")
    ref = ctx.evidence_packet.request_context_ref
    for attr in ("user_id", "league_id", "league_team_id"):
        if str(getattr(ref, attr, "")) != str(getattr(ctx.context, attr)):
            errors.append(f"evidence_packet {attr} mismatch")
    if ctx.evidence_packet.plan_type != ctx.decision_plan.plan_type or ctx.evidence_packet.decision_engine != ctx.decision_plan.decision_engine:
        errors.append("evidence_packet plan metadata mismatch")
    if ctx.rules_evaluation.evaluation_type != ctx.decision_plan.plan_type:
        errors.append("rules_evaluation plan metadata mismatch")
    if ctx.calculation_packet.plan_type != ctx.decision_plan.plan_type or ctx.calculation_packet.decision_engine != ctx.decision_plan.decision_engine:
        errors.append("calculation_packet plan metadata mismatch")
    if ctx.recommendation_validation.decision_action and ctx.recommendation_validation.decision_action != ctx.decision_output.action:
        errors.append("validation decision action mismatch")
    if ctx.recommendation_validation.recommendation_status and ctx.recommendation_validation.recommendation_status != ctx.decision_output.recommendation_status:
        errors.append("validation recommendation status mismatch")
    known = _known_evidence_ids(ctx.evidence_packet)
    for ref_id in _decision_entity_refs(ctx.decision_output):
        if ref_id not in known and not ref_id.startswith(("evidence_", "calculation_", "rules_", "owner_")):
            errors.append(f"decision reference {ref_id} missing from evidence")
    return errors


def _blocked_packet(ctx: _AnswerContext, errors: list[str], audit: list[AnswerAuditEvent]) -> AnswerPacket:
    limitations = [
        AnswerLimitation("alignment_blocked", error, "The answer must be limited to safe scope and validation failure language.", ["request_context", "answer_packet"])
        for error in errors[:MAX_LIMITATIONS]
    ]
    return AnswerPacket(
        answer_mode=AnswerMode.BLOCKED.value,
        response_status=ResponseStatus.BLOCKED.value,
        direct_answer=AnswerClaim("blocked", "I cannot safely answer this request from the current scoped assistant packets.", "blocked", ["answer_packet", "validation"], "unavailable"),
        recommendation=None,
        facts=_facts(ctx, AnswerMode.BLOCKED.value),
        rule_conclusions=_rules(ctx),
        calculations=_calculations(ctx),
        limitations=limitations,
        forbidden_claims=[ForbiddenAnswerClaim("replacement_recommendation", "Do not create a replacement recommendation when Stage 11 alignment is blocked.", ["answer_packet"])],
        source_index=[],
        audit_events=(audit + [AnswerAuditEvent("answer_alignment", "blocked", "Stage 1-10 packet alignment failed.", ["answer_packet"])])[:MAX_AUDIT],
        approved_for_explanation=False,
        approved_for_action=False,
        reduced_mode=True,
        confidence="unavailable",
    )


def _answer_mode(ctx: _AnswerContext) -> str:
    validation_status = ctx.recommendation_validation.validation_status
    if validation_status == ValidationStatus.FAILED.value:
        return AnswerMode.FAILED.value
    if validation_status == ValidationStatus.BLOCKED.value:
        return AnswerMode.BLOCKED.value
    if ctx.decision_output.recommendation_status == "unsupported" or ctx.decision_plan.plan_type == "unsupported_plan":
        return AnswerMode.UNSUPPORTED.value
    if _user_resolvable_ambiguity(ctx):
        return AnswerMode.CLARIFICATION_REQUIRED.value
    if ctx.decision_output.decision_type == "factual_response":
        return AnswerMode.DIRECT_FACT.value
    if ctx.decision_output.decision_type == "rules_response":
        return AnswerMode.DIRECT_RULES.value
    if (
        ctx.decision_output.decision_type == "draft_recommendation"
        and ctx.decision_output.recommendation_status == "insufficient_information"
    ):
        return AnswerMode.LIMITED_INFORMATION.value
    if ctx.decision_output.decision_type in {"player_comparison", "team_comparison", "league_analysis"}:
        return AnswerMode.COMPARISON.value
    if ctx.decision_output.decision_type in {"trade_discovery", "draft_recommendation", "free_agent_recommendation"} and ctx.recommendation_validation.approved_for_explanation:
        return AnswerMode.RANKED_OPTIONS.value
    if not ctx.recommendation_validation.approved_for_explanation:
        return AnswerMode.LIMITED_INFORMATION.value
    if ctx.recommendation_validation.validation_status == ValidationStatus.APPROVED_WITH_CONDITIONS.value:
        return AnswerMode.CONDITIONAL_RECOMMENDATION.value
    if ctx.decision_output.decision_type == "general_conversation":
        return AnswerMode.GENERAL_CONVERSATION.value
    return AnswerMode.RECOMMENDATION.value


def _response_status(ctx: _AnswerContext, mode: str) -> str:
    if mode == AnswerMode.FAILED.value:
        return ResponseStatus.FAILED.value
    if mode == AnswerMode.BLOCKED.value:
        return ResponseStatus.BLOCKED.value
    if mode == AnswerMode.UNSUPPORTED.value:
        return ResponseStatus.UNSUPPORTED.value
    if mode == AnswerMode.CLARIFICATION_REQUIRED.value:
        return ResponseStatus.CLARIFICATION_REQUIRED.value
    if mode == AnswerMode.LIMITED_INFORMATION.value:
        return ResponseStatus.LIMITED.value
    if ctx.recommendation_validation.validation_status == ValidationStatus.APPROVED_WITH_CONDITIONS.value:
        return ResponseStatus.READY_WITH_CONDITIONS.value
    if ctx.recommendation_validation.validation_status == ValidationStatus.APPROVED_WITH_WARNINGS.value or ctx.recommendation_validation.warnings:
        return ResponseStatus.READY_WITH_WARNINGS.value
    return ResponseStatus.READY.value


def _direct_answer(ctx: _AnswerContext, mode: str, status: str) -> AnswerClaim:
    refs = ["decision", "validation"]
    if mode == AnswerMode.DIRECT_FACT.value:
        text = _factual_claim_text(ctx) or "Here is the verified information available for this request."
        return AnswerClaim("factual_conclusion", text, status, _dedupe(refs + ["evidence"]), _confidence(ctx, status, _calculations(ctx), [], []))
    if mode == AnswerMode.DIRECT_RULES.value:
        taxi_violation = next(
            (
                violation for violation in ctx.rules_evaluation.violations
                if "taxi" in str(violation.rule_type or violation.violation_type).lower()
            ),
            None,
        )
        if taxi_violation:
            return AnswerClaim("legal_conclusion", f"No. {taxi_violation.explanation}", status, _dedupe(refs + ["rules"]), ctx.rules_evaluation.confidence)
        text = f"The deterministic rules result is {ctx.rules_evaluation.overall_status}."
        return AnswerClaim("legal_conclusion", text, status, _dedupe(refs + ["rules"]), ctx.rules_evaluation.confidence)
    if mode == AnswerMode.CLARIFICATION_REQUIRED.value:
        return AnswerClaim("clarification", _clarification_prompt(ctx), status, ["interpretation", "conversation_state"], "low")
    if mode == AnswerMode.LIMITED_INFORMATION.value:
        raw = (ctx.interpreted_question.raw_question or "").lower()
        if "draft" in raw and ctx.interpreted_question.pick_refs:
            return AnswerClaim("limited_draft_context", _draft_limited_text(ctx), status, _dedupe(refs + ["evidence"]), "low")
        return AnswerClaim("no_supported_recommendation", "I cannot validate a supported recommendation from the available data.", status, refs, "unavailable")
    if mode == AnswerMode.BLOCKED.value:
        return AnswerClaim("blocked", "I cannot safely answer this request from the current scoped data.", status, refs, "unavailable")
    if mode == AnswerMode.UNSUPPORTED.value:
        return AnswerClaim("unsupported", "This request is not supported by the authorized deterministic assistant engines.", status, ["decision_plan", "decision"], "unavailable")
    if mode == AnswerMode.FAILED.value:
        return AnswerClaim("failed", "I cannot safely assemble an answer from the current assistant packets.", status, ["answer_packet"], "unavailable")
    if ctx.recommendation_validation.approved_for_explanation:
        action = ctx.decision_output.action.replace("_", " ")
        if action in {"not applicable", "no decision"}:
            return AnswerClaim("no_structured_recommendation", "This question does not require a structured transaction recommendation.", status, refs, ctx.recommendation_validation.confidence_after_validation)
        return AnswerClaim("recommendation_conclusion", f"The validated recommendation is to {action}.", status, refs, ctx.recommendation_validation.confidence_after_validation)
    return AnswerClaim("limited", "Only limited verified context can be explained.", status, refs, "low")


def _recommendation(ctx: _AnswerContext, conditions: list[AnswerCondition]) -> AnswerRecommendation | None:
    if not ctx.recommendation_validation.approved_for_explanation:
        return None
    option = ctx.decision_output.primary_recommendation
    if not option:
        return None
    actionable = bool(ctx.recommendation_validation.approved_for_action and ctx.decision_output.actionable_now)
    if any(condition.blocking and condition.satisfied is not True for condition in conditions):
        actionable = False
    return AnswerRecommendation(
        action=ctx.decision_output.action,
        label=option.label,
        explanation=option.explanation,
        actionable_now=actionable,
        recommendation_status=ctx.decision_output.recommendation_status,
        related_entity_ids=_dedupe(option.related_entity_ids),
        source_refs=_dedupe(["decision", "validation"] + option.evidence_refs + option.calculation_refs),
        condition_refs=[condition.condition_id for condition in conditions],
    )


def _facts(ctx: _AnswerContext, mode: str) -> list[AnswerFact]:
    out: list[AnswerFact] = []
    relevant_ids = set(_decision_entity_refs(ctx.decision_output))
    if not relevant_ids:
        relevant_ids.add(ctx.context.league_team_id)
    for cap in ctx.evidence_packet.cap_evidence:
        if cap.league_team_id in relevant_ids or mode == AnswerMode.DIRECT_FACT.value:
            if cap.available_cap is not None:
                out.append(AnswerFact(f"cap:{cap.league_team_id}:{cap.season}", "cap_space", "Available cap", cap.available_cap, "cap dollars", [cap.league_team_id], ["cap_evidence"], "current_season"))
    for contract in ctx.evidence_packet.contract_evidence:
        if contract.player_id in relevant_ids or mode == AnswerMode.DIRECT_FACT.value:
            player_name = contract.contract_terms.get("player_name") or contract.player_id
            out.append(AnswerFact(
                f"contract:{contract.player_id}",
                "contract",
                f"{player_name} contract",
                {"player": player_name, "salary": contract.salary, "years_remaining": contract.years_remaining, "status": contract.contract_status},
                "cap dollars",
                [contract.player_id],
                ["contract_evidence"],
                "current_season",
            ))
    for pick in ctx.evidence_packet.draft_pick_evidence:
        if pick.canonical_pick_id in relevant_ids or pick.current_owner_team_id in relevant_ids or mode in {AnswerMode.DIRECT_FACT.value, AnswerMode.RANKED_OPTIONS.value}:
            out.append(AnswerFact(f"pick:{pick.canonical_pick_id or len(out)}", "draft_pick_ownership", "Draft pick ownership", {"season": pick.season, "round": pick.round, "slot": pick.slot, "verified_ownership": pick.verified_ownership}, None, _dedupe([pick.canonical_pick_id, pick.current_owner_team_id]), ["draft_pick_evidence"], "current_season"))
    for player in ctx.evidence_packet.player_evidence:
        if player.player_id in relevant_ids or mode in {AnswerMode.DIRECT_FACT.value, AnswerMode.RECOMMENDATION.value, AnswerMode.RANKED_OPTIONS.value, AnswerMode.COMPARISON.value}:
            out.append(AnswerFact(f"player:{player.player_id}", "player_profile", player.canonical_name or player.player_id, {"position": player.position, "team": player.nfl_team, "fantasy_team_id": player.fantasy_team_id, "is_free_agent": player.is_free_agent}, None, [player.player_id], ["player_evidence"], "stored"))
    for team in ctx.evidence_packet.team_evidence:
        if team.league_team_id in relevant_ids or team.league_team_id == ctx.context.league_team_id:
            value = {"owner_name": team.owner_name}
            if team.roster_player_ids:
                value["roster_count"] = len(team.roster_player_ids)
            out.append(AnswerFact(f"team:{team.league_team_id}", "team_context", team.team_name or team.league_team_id, value, None, [team.league_team_id], ["team_evidence"], "stored"))
    return out[:MAX_FACTS]


def _rules(ctx: _AnswerContext) -> list[AnswerRuleConclusion]:
    out = [
        AnswerRuleConclusion("overall_rules_status", ctx.rules_evaluation.overall_status, f"Rules evaluation is {ctx.rules_evaluation.overall_status}.", ctx.rules_evaluation.blocking_violation, ["rules"])
    ]
    for violation in ctx.rules_evaluation.violations:
        out.append(AnswerRuleConclusion(violation.rule_type, "violated", violation.explanation, violation.blocking, ["rules.violations"]))
    for condition in ctx.rules_evaluation.conditions:
        out.append(AnswerRuleConclusion(condition.condition_type, "conditional", condition.explanation, condition.blocking_if_unsatisfied, ["rules.conditions"]))
    for unresolved in ctx.rules_evaluation.unresolved_rules:
        out.append(AnswerRuleConclusion(unresolved.rule_type, "unresolved", unresolved.explanation, unresolved.blocking, ["rules.unresolved_rules"]))
    return _dedupe_rules(out)[:MAX_RULES]


def _calculations(ctx: _AnswerContext) -> list[AnswerCalculation]:
    wanted = set()
    if ctx.decision_output.primary_recommendation:
        wanted.update(ctx.decision_output.primary_recommendation.calculation_refs)
    for calc_ref in ctx.decision_output.supporting_calculations:
        wanted.update([calc_ref.calculation_type, calc_ref.output_key])
    if not wanted:
        wanted.update(result.calculation_type for result in ctx.calculation_packet.results[:MAX_CALCULATIONS])
    out = []
    for result in ctx.calculation_packet.results:
        if result.calculation_type not in wanted and result.output_key not in wanted:
            continue
        out.append(_answer_calculation(result))
    return out[:MAX_CALCULATIONS]


def _answer_calculation(result: CalculationResult) -> AnswerCalculation:
    return AnswerCalculation(
        result.calculation_type,
        result.output_key or result.calculation_type,
        _compact(result.value),
        result.unit,
        result.exact,
        result.estimated,
        result.method,
        [f"calculation:{result.calculation_type}"],
        result.assumptions,
    )


def _reasons(ctx: _AnswerContext) -> list[AnswerReason]:
    priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    factors = sorted(ctx.decision_output.reasoning_factors, key=lambda item: priority.get(item.importance, 4))
    out = [
        AnswerReason(item.factor_type, item.importance, item.direction, item.explanation, _dedupe(item.source_refs))
        for item in factors
        if item.source_refs
    ]
    for risk in ctx.decision_output.risks[:3]:
        out.append(AnswerReason(risk.risk_type, risk.severity, "risk", risk.explanation, _dedupe(risk.source_refs)))
    for tradeoff in ctx.decision_output.tradeoffs[:3]:
        out.append(AnswerReason("tradeoff", tradeoff.importance, "tradeoff", tradeoff.explanation, ["decision.tradeoffs"]))
    return out[:MAX_REASONS]


def _conditions(ctx: _AnswerContext) -> list[AnswerCondition]:
    out = []
    for index, item in enumerate(ctx.recommendation_validation.conditions):
        out.append(AnswerCondition(f"condition:{item.condition_type}:{index}", item.condition_type, item.explanation, item.satisfied, item.blocking, _dedupe(item.source_refs)))
    for index, item in enumerate(ctx.decision_output.conditions):
        out.append(AnswerCondition(f"decision_condition:{item.condition_type}:{index}", item.condition_type, item.explanation, item.satisfied, item.blocking, _dedupe(item.related_refs)))
    return _dedupe_conditions(out)[:MAX_CONDITIONS]


def _warnings(ctx: _AnswerContext) -> list[AnswerWarning]:
    out = [
        AnswerWarning(item.issue_type, item.explanation, item.severity, _dedupe(item.source_refs))
        for item in ctx.recommendation_validation.warnings
    ]
    for warning in ctx.evidence_packet.warnings[:2]:
        out.append(AnswerWarning("evidence_warning", warning, "warning", ["evidence"]))
    for warning in ctx.rules_evaluation.warnings[:2]:
        out.append(AnswerWarning("rules_warning", warning, "warning", ["rules"]))
    for warning in ctx.calculation_packet.warnings[:2]:
        out.append(AnswerWarning("calculation_warning", warning, "warning", ["calculation"]))
    for calc in ctx.calculation_packet.results:
        if calc.estimated:
            out.append(AnswerWarning("estimated_calculation", f"{calc.calculation_type} is estimated, not exact.", "warning", [f"calculation:{calc.calculation_type}"]))
    return out[:MAX_WARNINGS]


def _limitations(ctx: _AnswerContext, mode: str) -> list[AnswerLimitation]:
    out = []
    for item in ctx.recommendation_validation.errors:
        out.append(AnswerLimitation(item.issue_type, item.explanation, "Recommendation cannot be communicated as supported.", _dedupe(item.source_refs)))
    for item in ctx.recommendation_validation.missing_support:
        out.append(AnswerLimitation(item.support_type, item.explanation, "Answer is limited by missing support.", _dedupe(item.missing_refs)))
    for item in ctx.recommendation_validation.contradictions:
        out.append(AnswerLimitation(item.contradiction_type, item.explanation, "Contradiction prevents action approval or explanation.", _dedupe(item.source_refs)))
    for unresolved in ctx.calculation_packet.unresolved_calculations:
        out.append(AnswerLimitation(unresolved.calculation_type, unresolved.explanation, "Do not make a positive numeric claim for this missing calculation.", _dedupe(unresolved.missing_inputs)))
    for unresolved in ctx.rules_evaluation.unresolved_rules:
        out.append(AnswerLimitation(unresolved.rule_type, unresolved.explanation, "Rules conclusion is limited by unavailable rule evidence.", _dedupe(unresolved.missing_evidence)))
    for unresolved in ctx.evidence_packet.unresolved_requirements:
        out.append(AnswerLimitation(unresolved.requirement_type, unresolved.explanation, "Evidence is incomplete for this dimension.", _dedupe(unresolved.related_entity_ids)))
    if mode == AnswerMode.LIMITED_INFORMATION.value:
        out.append(AnswerLimitation("validation_not_approved", "Validation did not approve a recommendation for explanation.", "Explain verified facts only; do not recommend a replacement.", ["validation"]))
    return out[:MAX_LIMITATIONS]


def _alternatives(ctx: _AnswerContext) -> list[AnswerAlternative]:
    out = []
    for option in ctx.decision_output.alternatives[:MAX_ALTERNATIVES]:
        out.append(AnswerAlternative(option.label, option.action, option.explanation, option.rank, _dedupe(["decision.alternatives"] + option.evidence_refs + option.calculation_refs)))
    return out


def _forbidden_claims(
    ctx: _AnswerContext,
    recommendation: AnswerRecommendation | None,
    conditions: list[AnswerCondition],
    calculations: list[AnswerCalculation],
    limitations: list[AnswerLimitation],
) -> list[ForbiddenAnswerClaim]:
    out = [
        ForbiddenAnswerClaim("executed_action", "Do not say any trade, roster move, release, extension, acquisition, draft pick, or lineup change was executed.", ["answer_packet"]),
    ]
    if not recommendation:
        out.append(ForbiddenAnswerClaim("replacement_recommendation", "Do not create a replacement recommendation.", ["validation"]))
        out.append(ForbiddenAnswerClaim("rejected_as_supported", "Do not present a rejected, blocked, failed, or unvalidated recommendation as supported.", ["validation"]))
    if not ctx.recommendation_validation.approved_for_action:
        out.append(ForbiddenAnswerClaim("actionability", "Do not say this recommendation is ready to execute now.", ["validation.approved_for_action"]))
    if ctx.rules_evaluation.overall_status == "conditionally_legal" or any(condition.blocking and condition.satisfied is not True for condition in conditions):
        out.append(ForbiddenAnswerClaim("confirmed_legality", "Do not say the action is confirmed legal while conditions remain unresolved.", ["rules", "conditions"]))
    if any(calc.estimated for calc in calculations):
        out.append(ForbiddenAnswerClaim("exact_estimate", "Do not call estimated calculations exact.", ["calculations"]))
    if any(item.limitation_type in {"free_agent_availability_missing", "availability_unverified"} for item in limitations):
        out.append(ForbiddenAnswerClaim("availability_claim", "Do not claim an unavailable or unverified player can be acquired.", ["evidence"]))
    if ctx.decision_output.decision_type in {"trade_construction", "trade_evaluation"}:
        out.append(ForbiddenAnswerClaim("acceptance_probability", "Do not claim the other owner will accept unless a verified source supports it.", ["decision"]))
    if ctx.decision_output.decision_type in {"draft_recommendation", "draft_pick_evaluation"}:
        out.append(ForbiddenAnswerClaim("future_exact_pick_slot", "Do not claim a future pick has an exact slot unless verified pick evidence supplies it.", ["draft_pick_evidence"]))
    return _dedupe_forbidden(out)[:MAX_FORBIDDEN]


def _source_index(*groups: Any) -> list[AnswerSourceReference]:
    refs: list[str] = []
    for group in groups:
        refs.extend(_collect_refs(group))
    out = []
    for ref in _dedupe(refs):
        packet_type = _packet_type(ref)
        out.append(AnswerSourceReference(ref, packet_type, packet_type, _source_label(ref), _entity_ids_from_ref(ref)))
    return out[:MAX_SOURCES]


def _audit(ctx: _AnswerContext) -> list[AnswerAuditEvent]:
    return [
        AnswerAuditEvent("request_context", "ready", "Authenticated league and team scope supplied.", ["request_context"]),
        AnswerAuditEvent("conversation_state", "ready" if ctx.conversation_state else "not_applicable", "Conversation state loaded when available.", ["conversation_state"]),
        AnswerAuditEvent("interpretation", ctx.interpreted_question.confidence, "Question interpretation supplied.", ["interpretation"]),
        AnswerAuditEvent("objective", ctx.owner_objective.confidence, "Owner objective supplied.", ["objective"]),
        AnswerAuditEvent("planning", ctx.decision_plan.confidence, "Decision plan selected.", ["decision_plan"]),
        AnswerAuditEvent("evidence", ctx.evidence_packet.execution_status, "Evidence packet supplied.", ["evidence"]),
        AnswerAuditEvent("rules", ctx.rules_evaluation.overall_status, "Rules evaluation supplied.", ["rules"]),
        AnswerAuditEvent("calculations", ctx.calculation_packet.execution_status, "Calculation packet supplied.", ["calculation"]),
        AnswerAuditEvent("decision", ctx.decision_output.recommendation_status, "Deterministic decision supplied.", ["decision"]),
        AnswerAuditEvent("validation", ctx.recommendation_validation.validation_status, "Recommendation validation supplied as final gate.", ["validation"]),
    ]


def _user_resolvable_ambiguity(ctx: _AnswerContext) -> bool:
    if any(item.blocking for item in ctx.interpreted_question.ambiguities):
        return True
    if ctx.decision_plan.blockers:
        return any(str(blocker.blocker_type) in {"missing_requested_player", "ambiguous_player", "missing_trade_side", "missing_contract_terms", "missing_requested_pick"} and blocker.resolvable for blocker in ctx.decision_plan.blockers)
    return False


def _clarification_prompt(ctx: _AnswerContext) -> str:
    for ambiguity in ctx.interpreted_question.ambiguities:
        if ambiguity.blocking:
            if ambiguity.candidates:
                return f"Which {ambiguity.raw_text} do you mean?"
            return ambiguity.explanation or "Which specific player, team, pick, or terms do you mean?"
    for blocker in ctx.decision_plan.blockers:
        if blocker.resolvable:
            if "contract" in blocker.blocker_type:
                return "What contract terms are you considering?"
            if "trade_side" in blocker.blocker_type:
                return "Are you offering the asset or receiving it?"
            if "pick" in blocker.blocker_type:
                return "Which pick or draft slot do you mean?"
    return "Which specific player, team, pick, or terms do you mean?"


def _factual_claim_text(ctx: _AnswerContext) -> str | None:
    raw = (ctx.interpreted_question.raw_question or "").lower()
    if is_scenario_question(raw):
        scenario = next((item for item in ctx.evidence_packet.transaction_evidence if item.transaction_type == "scenario_simulation" or item.summary), None)
        if scenario and scenario.summary:
            text = scenario.summary
            if _scenario_conflicts_with_owner_constraints(ctx, scenario):
                text += " This also conflicts with your explicit preference to preserve future first-round picks."
            return text
        return "I could not simulate that scenario from the current scoped team data."
    if is_team_identity_question(raw):
        for team in ctx.evidence_packet.team_evidence:
            if team.league_team_id == ctx.context.league_team_id:
                label = team.team_name or ctx.context.team_name or team.owner_name or ctx.context.owner_name
                if label:
                    league = _league_name_from_team(team)
                    suffix = f" in {league}" if league else ""
                    return f"You manage {label}{suffix}."
        if ctx.context.team_name or ctx.context.owner_name:
            return f"You manage {ctx.context.team_name or ctx.context.owner_name}."
    if is_roster_list_question(raw):
        rows = _roster_players(ctx)
        if not rows:
            return "No verified active roster players were found for your team."
        lines = [f"You currently have {len(rows)} players on your roster:"]
        lines.extend(f"- {_roster_player_label(player)}" for player in rows)
        return "\n".join(lines)
    if is_league_owner_intelligence_question(raw):
        return _league_owner_intelligence_text(ctx, raw)
    if is_football_intelligence_question(raw):
        return _football_intelligence_text(ctx, raw)
    if "one year" in raw and "contract" in raw:
        matches = [contract for contract in ctx.evidence_packet.contract_evidence if contract.league_team_id == ctx.context.league_team_id and contract.years_remaining == 1]
        if matches:
            names = [
                contract.contract_terms.get("player_name")
                or next((player.canonical_name for player in ctx.evidence_packet.player_evidence if player.player_id == contract.player_id), contract.player_id)
                for contract in matches
            ]
            return f"Players with one year left: {', '.join(names)}."
        return "No verified active-team contracts with one year left were found."
    if "best" in raw and "worst" in raw and "contract" in raw:
        return _contract_comparison_text(ctx)
    if "cap space" in raw or "cap room" in raw or "how much cap" in raw:
        cap = _cap_for_answer(ctx)
        if cap and cap.available_cap is not None:
            season = cap.season or ctx.context.requested_season
            if cap.available_cap < 0:
                value = _format_number(abs(cap.available_cap))
                return f"You are {value} cap dollars over the cap for {season}."
            value = _format_number(cap.available_cap)
            if cap.available_cap == 0:
                return f"You are exactly at the cap for {season}."
            return f"You have {value} cap dollars in cap space for {season}."
        return "I could not verify current cap space from the scoped cap evidence."
    if "draft" in raw and ctx.interpreted_question.pick_refs:
        return _draft_limited_text(ctx)
    if "what happens" in raw and "cap" in raw and any(term in raw for term in ("release", "drop", "cut")):
        player_name = None
        salary = None
        for contract in ctx.evidence_packet.contract_evidence:
            if contract.league_team_id == ctx.context.league_team_id:
                player_name = contract.contract_terms.get("player_name") or contract.player_id
                salary = contract.salary
                break
        dead_cap = _calculation_value(ctx, "dead_cap_impact")
        savings = _calculation_value(ctx, "release_savings")
        post_cap = _calculation_value(ctx, "post_transaction_cap")
        if player_name and dead_cap is not None and savings is not None and post_cap is not None:
            salary_text = f" removes {salary} of active salary," if salary is not None else ""
            return f"Releasing {player_name}{salary_text} creates {dead_cap} of dead cap, changes cap by {savings}, and leaves projected cap space at {post_cap}."
    for fact in _facts(ctx, AnswerMode.DIRECT_FACT.value):
        if fact.fact_type == "cap_space":
            return f"You have {_format_number(fact.value)} cap dollars in cap space for the requested season."
        if fact.fact_type == "contract":
            return f"The verified contract fact available is {fact.label}."
        if fact.fact_type == "draft_pick_ownership":
            return "The verified draft-pick ownership details are available in the answer facts."
    return None


def _league_owner_intelligence_text(ctx: _AnswerContext, raw: str) -> str | None:
    loi = ctx.league_owner_intelligence_context
    if not loi or getattr(loi, "availability", None) != "available":
        return "I could not verify league owner history from the current scoped league data."
    profiles = list(getattr(loi, "profiles", []) or [])
    if not profiles:
        return "I could not verify league owner history from the current scoped league data."
    if "trade" in raw and ("most" in raw or "active" in raw):
        ranked = sorted(profiles, key=lambda item: item.activity_summary.completed_trades, reverse=True)
        leader = ranked[0]
        if leader.activity_summary.completed_trades <= 0:
            return "I do not have verified trade history for any team in this league."
        return f"{leader.identity.display_name} has the most verified trade activity with {leader.activity_summary.completed_trades} completed trades in the recorded window."
    if "future first" in raw and ("own" in raw or "most" in raw):
        ranked = sorted(profiles, key=lambda item: int(item.current_state.future_pick_counts_by_round.get("1", 0)), reverse=True)
        leader = ranked[0]
        count = int(leader.current_state.future_pick_counts_by_round.get("1", 0))
        if count <= 0:
            return "I do not have verified future first-round pick ownership for any team in this league."
        return f"{leader.identity.display_name} owns the most verified future first-round picks with {count}."
    if "future pick" in raw and "acquired" in raw:
        ranked = sorted(profiles, key=lambda item: item.activity_summary.asset_movement.picks_acquired, reverse=True)
        leader = ranked[0]
        count = leader.activity_summary.asset_movement.picks_acquired
        if count <= 0:
            return "I do not have verified future-pick acquisition history for any team in this league."
        return f"{leader.identity.display_name} has acquired the most verified picks in recorded transactions with {count}."
    if "cap space" in raw or "cap room" in raw:
        with_cap = [profile for profile in profiles if profile.current_state.available_cap is not None]
        if not with_cap:
            return "I do not have verified cap-space context for the league."
        leader = sorted(with_cap, key=lambda item: item.current_state.available_cap or 0, reverse=True)[0]
        return f"{leader.identity.display_name} has the most verified cap space at {_format_number(leader.current_state.available_cap)} cap dollars."
    if "have i traded with" in raw:
        requested = [ref.canonical_id for ref in ctx.interpreted_question.fantasy_team_refs if ref.canonical_id and ref.canonical_id != ctx.context.league_team_id]
        if not requested:
            return "Which active-league team do you want me to check?"
        target = profiles[0].identity.league_team_id
        for profile in profiles:
            if profile.identity.league_team_id == requested[0]:
                target = profile.identity.league_team_id
                history = [item for item in profile.trade_partner_history if ctx.context.league_team_id in {item.team_a_id, item.team_b_id}]
                if not history:
                    return f"I do not have verified recorded trades between your team and {profile.identity.display_name}."
                return f"Yes. Your team and {profile.identity.display_name} have {sum(item.verified_trade_count for item in history)} verified recorded trades."
        return f"I could not resolve that team inside the active league."
    if "what trades has" in raw:
        requested = [ref.canonical_id for ref in ctx.interpreted_question.fantasy_team_refs if ref.canonical_id]
        target_id = requested[0] if requested else None
        profile = loi.profile_for_team(target_id) if target_id else None
        if not profile:
            return "Which active-league team do you want trade history for?"
        return f"{profile.identity.display_name} has {profile.activity_summary.completed_trades} verified completed trades in the recorded window."
    if "need" in raw:
        position = _requested_position(raw)
        if not position:
            return "I can answer roster-count needs by position when the position is specified."
        thin = [profile for profile in profiles if int(profile.current_state.positional_counts.get(position, 0)) <= 1]
        if not thin:
            return f"No team is verified with one or fewer {position} players in the current roster facts."
        names = ", ".join(profile.identity.display_name for profile in thin[:5])
        return f"Based only on verified roster counts, these teams have one or fewer {position} players: {names}."
    if "assets to trade for" in raw:
        capable = [profile for profile in profiles if (profile.current_state.available_cap or 0) > 0 or sum(profile.current_state.future_pick_counts_by_round.values()) > 0]
        if not capable:
            return "I do not have verified cap or pick-asset context for possible counterparties."
        names = ", ".join(profile.identity.display_name for profile in capable[:5])
        return f"Based on verified cap and pick assets only, these teams have possible transaction capacity: {names}."
    return "I can summarize verified team-management behavior from scoped league history, but I cannot infer another owner's intent."


def _football_intelligence_text(ctx: _AnswerContext, raw: str) -> str | None:
    football = ctx.football_intelligence_context
    if not football or getattr(football, "availability", None) not in {"available", "partial"}:
        return "I could not verify enough scoped roster-construction data to answer that football-structure question."
    roster = getattr(football, "roster_construction", None)
    if not roster:
        return "I could not verify enough scoped roster-construction data to answer that football-structure question."

    requested_position = _requested_position(raw)
    if requested_position:
        group = football.group(requested_position)
        if not group:
            return f"I could not verify any {requested_position} players on your scoped roster."
        requirement = f" against {group.required_starters} direct starting slots" if group.required_starters is not None else ""
        return f"Your {requested_position} room has {group.active_count} active players, {group.taxi_count} taxi players, and {group.ir_count} IR players{requirement}."

    if "need" in raw or "weakness" in raw or "thinnest" in raw or "hole" in raw:
        if roster.needs:
            lines = ["Your most visible structural needs from verified roster data are:"]
            lines.extend(f"- {need.label}: {need.explanation}" for need in roster.needs[:5])
            return "\n".join(lines)
        return "I do not see a verified immediate starter shortage or premium future-pick limitation from the current scoped football context."

    if "strong" in raw or "strength" in raw:
        if roster.strengths:
            lines = ["Your clearest structural strengths from verified roster data are:"]
            lines.extend(f"- {strength.label}: {strength.explanation}" for strength in roster.strengths[:5])
            return "\n".join(lines)
        return "I do not have enough verified lineup-rule and roster-depth data to label a structural strength."

    if "contract" in raw or "salary" in raw:
        risks = [risk for risk in roster.risks if "contract" in risk.rule_id or "salary" in risk.rule_id]
        exposure = roster.contract_exposure
        if risks:
            lines = ["Verified contract and salary risks:"]
            lines.extend(f"- {risk.label}: {risk.explanation}" for risk in risks[:5])
            return "\n".join(lines)
        if exposure and exposure.committed_salary is not None:
            return f"I verified {exposure.committed_salary} committed salary with {exposure.expiring_contract_count} expiring contracts, and no deterministic contract-risk rule fired."
        return "I could not verify enough contract or salary data to assess structural contract exposure."

    if "age" in raw or "veteran" in raw or "rookie" in raw:
        age = roster.age_curve
        if not age or age.known_age_count == 0:
            return "I could not verify enough age data to assess roster age structure."
        return f"Your roster age profile has {age.known_age_count} verified ages, median age {age.median_age}, {age.rookie_count} rookies, and {age.veteran_count} veterans."

    if "contender" in raw or "rebuild" in raw or "balanced" in raw or "balance" in raw or "structure" in raw or "construction" in raw:
        dimensions = ", ".join(f"{item.dimension}: {item.status}" for item in roster.strategy_fit[:5])
        return f"From verified football context, your roster has {roster.active_roster_count} active players, {len(roster.strengths)} structural strengths, {len(roster.needs)} structural needs, and {len(roster.risks)} structural risks. Strategy-fit dimensions: {dimensions}."

    return f"From verified football context, your roster has {roster.active_roster_count} active players, {roster.taxi_count} taxi players, {roster.ir_count} IR players, {len(roster.strengths)} structural strengths, {len(roster.needs)} structural needs, and {len(roster.risks)} structural risks."


def _requested_position(raw: str) -> str | None:
    if "quarterback" in raw or " qb" in f" {raw}":
        return "QB"
    if "running back" in raw or " rb" in f" {raw}":
        return "RB"
    if "wide receiver" in raw or "receiver" in raw or " wr" in f" {raw}":
        return "WR"
    if "tight end" in raw or " te" in f" {raw}":
        return "TE"
    return None


def _cap_for_answer(ctx: _AnswerContext) -> Any | None:
    for cap in ctx.evidence_packet.cap_evidence:
        if cap.league_team_id == ctx.context.league_team_id:
            return cap
    return ctx.evidence_packet.cap_evidence[0] if ctx.evidence_packet.cap_evidence else None


def _draft_limited_text(ctx: _AnswerContext) -> str:
    pick = ctx.interpreted_question.pick_refs[0]
    label = f"{pick.round}.{pick.slot:02d}" if pick.round and pick.slot else pick.raw_text
    verified_pick = next(
        (
            item for item in ctx.evidence_packet.draft_pick_evidence
            if (pick.round is None or item.round == pick.round) and (pick.slot is None or item.slot == pick.slot)
        ),
        None,
    )
    if verified_pick and verified_pick.current_owner_team_id == ctx.context.league_team_id:
        return f"I can verify that you hold pick {label}, but I do not have a verified internal rookie prospect pool to recommend a player for that pick yet."
    if verified_pick:
        return f"I found pick {label}, but I cannot verify that your team currently owns it."
    return f"I cannot verify pick {label} in the current scoped draft-pick records, and I do not have a verified internal rookie prospect pool to recommend a player yet."


def _scenario_conflicts_with_owner_constraints(ctx: _AnswerContext, scenario: Any) -> bool:
    has_protected_first = any(
        constraint.constraint_type == "do_not_trade_first_round_pick"
        for constraint in ctx.owner_objective.non_negotiables
    )
    if not has_protected_first:
        return False
    raw = (ctx.interpreted_question.raw_question or "").lower()
    pick_text = " ".join(str(item) for item in scenario.pick_ids)
    return "first" in raw or "round_1" in pick_text or "1st" in pick_text


def _contract_comparison_text(ctx: _AnswerContext) -> str | None:
    contracts = [
        contract for contract in ctx.evidence_packet.contract_evidence
        if contract.league_team_id == ctx.context.league_team_id
        and contract.salary is not None
        and _contract_player_name(ctx, contract)
        and str(contract.contract_status or "active").strip().lower() not in {"released", "release", "cut", "dropped", "waived", "inactive_released"}
    ]
    if not contracts:
        return "I could not verify named active contracts for your team."
    scored = []
    value_by_player = {
        player.player_id: _player_value_for_contract(player)
        for player in ctx.evidence_packet.player_evidence
    }
    for contract in contracts:
        salary = float(contract.salary or 0)
        years = contract.years_remaining if contract.years_remaining is not None else 0
        value = value_by_player.get(contract.player_id)
        if value is not None and salary > 0:
            best_score = value / salary
            concern_score = salary / max(value, 1)
            basis = "stored value per cap dollar"
        else:
            best_score = -salary
            concern_score = salary + max(years, 0)
            basis = "contract-structure-only"
        scored.append((contract, best_score, concern_score, basis))
    best = sorted(scored, key=lambda item: item[1], reverse=True)[:3]
    worst = sorted(scored, key=lambda item: item[2], reverse=True)[:3]
    basis = "stored value where available, otherwise contract structure" if any(item[3] != "contract-structure-only" for item in scored) else "contract-structure-only"
    lines = [f"Using verified {basis} signals:"]
    lines.append("Best contract signals:")
    lines.extend(f"- {_contract_label_for_comparison(ctx, contract)}" for contract, _, _, _ in best)
    lines.append("Biggest contract concerns:")
    lines.extend(f"- {_contract_label_for_comparison(ctx, contract)}" for contract, _, _, _ in worst)
    return "\n".join(lines)


def _contract_label_for_comparison(ctx: _AnswerContext, contract: Any) -> str:
    name = _contract_player_name(ctx, contract) or "Unknown player"
    position = contract.contract_terms.get("player_position")
    salary = _format_number(contract.salary)
    years = "unknown years remaining" if contract.years_remaining is None else f"{contract.years_remaining} year{'s' if contract.years_remaining != 1 else ''} remaining"
    prefix = f"{name} - {position}: " if position else f"{name}: "
    return f"{prefix}{salary} cap dollars, {years}"


def _contract_player_name(ctx: _AnswerContext, contract: Any) -> str | None:
    return (
        contract.contract_terms.get("player_name")
        or next((player.canonical_name for player in ctx.evidence_packet.player_evidence if player.player_id == contract.player_id), None)
    )


def _player_value_for_contract(player: Any) -> float | None:
    for source in (player.league_relative_value, player.strategic_profile):
        if not isinstance(source, dict):
            continue
        for key in ("overall_value_score", "value_score", "asset_score", "dynasty_score", "win_now_score"):
            value = _safe_float(source.get(key))
            if value is not None:
                return value
    return None


def _format_number(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return str(value)
    return f"{number:.1f}"


def _safe_float(value: Any) -> float | None:
    try:
        text = str(value).strip().replace("$", "").replace(",", "")
        if text.lower() in {"", "none", "null", "nan"}:
            return None
        return float(text)
    except Exception:
        return None


def _calculation_value(ctx: _AnswerContext, calculation_type: str) -> Any:
    for result in ctx.calculation_packet.results:
        if result.calculation_type == calculation_type or result.output_key == calculation_type:
            return result.value
    return None


def _league_name_from_team(team: Any) -> str | None:
    for source in (team.roster_summary, team.team_brain_summary):
        value = source.get("league_name") or source.get("league")
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _roster_players(ctx: _AnswerContext) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for player in ctx.evidence_packet.player_evidence:
        if player.fantasy_team_id != ctx.context.league_team_id:
            continue
        status = str(player.status or "").strip().lower()
        if status in {"released", "release", "cut", "dropped", "waived", "inactive_released"}:
            continue
        key = player.player_id or player.canonical_name or ""
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(player)
    return out


def _roster_player_label(player: Any) -> str:
    parts = [str(player.canonical_name or player.player_id)]
    if player.position:
        parts.append(str(player.position))
    status = str(player.status or "").strip()
    if status and status.lower() not in {"active", "rostered"}:
        parts.append(status.upper() if status.lower() in {"ir", "taxi"} else status)
    return " - ".join(parts)


def _reduced_mode(ctx: _AnswerContext, status: str, warnings: list[AnswerWarning], limitations: list[AnswerLimitation]) -> bool:
    return bool(
        status in {ResponseStatus.READY_WITH_WARNINGS.value, ResponseStatus.LIMITED.value}
        or warnings
        or limitations
        or ctx.recommendation_validation.reduced_mode
        or ctx.decision_output.reduced_mode
        or ctx.evidence_packet.reduced_mode
        or ctx.rules_evaluation.reduced_mode
        or ctx.calculation_packet.reduced_mode
        or any(calc.estimated for calc in ctx.calculation_packet.results)
    )


def _confidence(ctx: _AnswerContext, status: str, calculations: list[AnswerCalculation], warnings: list[AnswerWarning], limitations: list[AnswerLimitation]) -> str:
    if status in {ResponseStatus.BLOCKED.value, ResponseStatus.UNSUPPORTED.value, ResponseStatus.FAILED.value}:
        return "unavailable"
    cap = ctx.recommendation_validation.confidence_after_validation or ctx.decision_output.confidence
    if status == ResponseStatus.LIMITED.value:
        return "low" if limitations else "medium"
    if any(calc.estimated for calc in calculations) or warnings or limitations:
        cap = _min_confidence(cap, "medium")
    if not ctx.evidence_packet.required_evidence_complete or not ctx.calculation_packet.required_calculations_complete:
        cap = _min_confidence(cap, "low")
    return cap if cap in {"high", "medium", "low", "unavailable"} else "medium"


def _known_evidence_ids(packet: EvidencePacket) -> set[str]:
    ids = {"evidence_packet", "player_evidence", "team_evidence", "contract_evidence", "cap_evidence", "draft_pick_evidence", "lineup_evidence", "free_agent_evidence"}
    ids.update(player.player_id for player in packet.player_evidence if player.player_id)
    ids.update(team.league_team_id for team in packet.team_evidence if team.league_team_id)
    ids.update(contract.player_id for contract in packet.contract_evidence if contract.player_id)
    ids.update(cap.league_team_id for cap in packet.cap_evidence if cap.league_team_id)
    ids.update(pick.canonical_pick_id for pick in packet.draft_pick_evidence if pick.canonical_pick_id)
    ids.update(pick.current_owner_team_id for pick in packet.draft_pick_evidence if pick.current_owner_team_id)
    ids.update(lineup.league_team_id for lineup in packet.lineup_evidence if lineup.league_team_id)
    ids.update(free.player_id for free in packet.free_agent_evidence if free.player_id)
    return {str(item) for item in ids if item}


def _decision_entity_refs(output: DecisionOutput) -> list[str]:
    refs = list(output.evidence_refs)
    if output.primary_recommendation:
        refs.extend(output.primary_recommendation.related_entity_ids)
    for option in output.alternatives:
        refs.extend(option.related_entity_ids)
    for item in output.rejected_options:
        refs.extend(item.related_entity_ids)
    return _dedupe(refs)


def _collect_refs(value: Any) -> list[str]:
    refs = []
    if value is None:
        return refs
    if isinstance(value, list):
        for item in value:
            refs.extend(_collect_refs(item))
        return refs
    if hasattr(value, "source_refs"):
        refs.extend(getattr(value, "source_refs") or [])
    if hasattr(value, "condition_refs"):
        refs.extend(getattr(value, "condition_refs") or [])
    if hasattr(value, "related_refs"):
        refs.extend(getattr(value, "related_refs") or [])
    return refs


def _packet_type(ref: str) -> str:
    text = ref.lower()
    if "rule" in text:
        return "rule"
    if "calculation" in text or text.startswith("calculation:"):
        return "calculation"
    if "objective" in text:
        return "objective"
    if "validation" in text:
        return "validation"
    if "decision" in text:
        return "decision"
    if "interpretation" in text:
        return "interpretation"
    if "conversation" in text:
        return "conversation_state"
    return "evidence"


def _source_label(ref: str) -> str:
    labels = {
        "player_evidence": "player evidence",
        "team_evidence": "team context",
        "contract_evidence": "contract record",
        "cap_evidence": "cap summary",
        "draft_pick_evidence": "draft-pick ownership",
        "free_agent_evidence": "free-agent availability",
        "lineup_evidence": "lineup evidence",
        "rules": "league rules",
        "calculation": "deterministic calculation",
        "decision": "validated decision",
        "validation": "recommendation validation",
        "objective": "owner objective",
    }
    return labels.get(ref.split(".")[0], ref.replace("_", " "))


def _entity_ids_from_ref(ref: str) -> list[str]:
    if ":" in ref:
        return [ref.split(":", 1)[1]]
    return []


def _dedupe(items: list[Any]) -> list[str]:
    out = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _dedupe_rules(items: list[AnswerRuleConclusion]) -> list[AnswerRuleConclusion]:
    out = []
    seen = set()
    for item in items:
        key = (item.rule_type, item.status, item.explanation)
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _dedupe_conditions(items: list[AnswerCondition]) -> list[AnswerCondition]:
    out = []
    seen = set()
    for item in items:
        key = (item.condition_type, item.explanation)
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _dedupe_forbidden(items: list[ForbiddenAnswerClaim]) -> list[ForbiddenAnswerClaim]:
    out = []
    seen = set()
    for item in items:
        if item.claim_type not in seen:
            out.append(item)
            seen.add(item.claim_type)
    return out


def _min_confidence(a: str, b: str) -> str:
    rank = {"unavailable": 0, "low": 1, "medium": 2, "high": 3}
    return a if rank.get(a, 1) <= rank.get(b, 1) else b


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
