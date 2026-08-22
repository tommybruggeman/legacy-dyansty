from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from gm_assistant.answer_packet import AnswerPacket
from gm_assistant.openai_reasoning.client import OpenAIReasoningProvider, UnavailableReasoningProvider
from gm_assistant.openai_reasoning.models import ProviderResult, ReasoningProvider, ReasoningRequest, ReasoningResponse, ReasoningTrace
from gm_assistant.openai_reasoning.prompt_builder import build_reasoning_request
from gm_assistant.openai_reasoning.validation import ReasoningValidation, validate_reasoning_response
from gm_assistant.rendered_answer import validate_rendered_answer


DIRECT_BYPASS_MARKERS = (
    "what team do i manage",
    "what team am i managing",
    "who is on my team",
    "who is on my roster",
    "show me my roster",
    "how much cap",
    "cap space",
    "what picks do i own",
    "how many quarterbacks",
    "do i have enough quarterback depth",
)

REASONING_MARKERS = (
    "should i",
    "make this trade",
    "would this trade",
    "fits my rebuild",
    "if i am contending",
    "prioritize",
    "offseason",
    "offseason strategy",
    "best way",
    "best player",
    "best players",
    "rank my roster",
    "rank the roster",
    "rank my entire roster",
    "rank my full roster",
    "rank all my players",
    "rank my players",
    "best-to-worst roster ranking",
    "best to worst roster ranking",
    "top five players",
    "top 5 players",
    "strongest players",
    "order my roster by value",
    "biggest strength",
    "biggest risk",
    "biggest weakness",
    "biggest roster needs",
    "roster needs",
    "fix my rb",
    "rb room",
    "market-check",
    "market check",
    "am i a contender",
    "championship window",
    "which option",
    "approach the second overall",
    "draft for need or value",
    "trade down",
)

REASONING_ELIGIBLE_INTENTS = {
    "player_evaluation",
    "player_comparison",
    "roster_evaluation",
    "trade_evaluation",
    "trade_discovery",
    "trade_construction",
    "draft_recommendation",
    "draft_pick_evaluation",
    "free_agent_recommendation",
    "contract_question",
    "salary_cap_question",
    "lineup_question",
    "roster_move_question",
    "long_term_planning",
    "team_comparison",
    "league_analysis",
    "scenario_simulation",
}


@dataclass(frozen=True)
class ReasoningEligibility:
    decision: str
    provider_allowed: bool
    reason: str


@dataclass(frozen=True)
class ReasonedAnswer:
    text: str
    provider_called: bool
    provider_result: ProviderResult | None
    validation: ReasoningValidation | None
    request: ReasoningRequest | None
    trace: ReasoningTrace


class OpenAIReasoningService:
    def __init__(self, provider: ReasoningProvider | None = None) -> None:
        self.provider = provider if provider is not None else OpenAIReasoningProvider.from_environment()

    def answer(
        self,
        *,
        question: str,
        context: Any,
        conversation_history: list[dict[str, str]] | None,
        conversation_state: Any,
        interpreted_question: Any,
        owner_objective: Any,
        decision_plan: Any,
        evidence_packet: Any,
        rules_evaluation: Any,
        calculation_packet: Any,
        decision_output: Any,
        recommendation_validation: Any,
        answer_packet: AnswerPacket,
        owner_intelligence_context: Any | None = None,
        league_owner_intelligence_context: Any | None = None,
        football_intelligence_context: Any | None = None,
    ) -> ReasonedAnswer:
        eligibility = determine_reasoning_eligibility(
            question=question,
            context=context,
            answer_packet=answer_packet,
            decision_plan=decision_plan,
            interpreted_question=interpreted_question,
        )
        fallback_text = validate_rendered_answer(answer_packet, None).approved_text
        request_id = _request_id(context, question)
        if not eligibility.provider_allowed:
            return ReasonedAnswer(
                text=fallback_text,
                provider_called=False,
                provider_result=None,
                validation=None,
                request=None,
                trace=ReasoningTrace(
                    request_id,
                    "skipped",
                    eligibility.decision,
                    provider_selected=type(self.provider).__name__.replace("ReasoningProvider", "") or "Unknown",
                    provider_called=False,
                    provider_skipped_reason=eligibility.reason,
                    result_status="skipped",
                    fallback_status="deterministic_fallback",
                    fallback_reason=eligibility.reason,
                    final_answer_source="deterministic_fallback",
                    safe_error_code=eligibility.reason,
                ),
            )
        request = build_reasoning_request(
            request_id=request_id,
            league_id=context.league_id,
            league_team_id=context.league_team_id,
            question=question,
            conversation_history=conversation_history or [],
            conversation_state=conversation_state,
            interpreted_question=interpreted_question,
            owner_objective=owner_objective,
            decision_plan=decision_plan,
            evidence_packet=evidence_packet,
            rules_evaluation=rules_evaluation,
            calculation_packet=calculation_packet,
            decision_output=decision_output,
            recommendation_validation=recommendation_validation,
            answer_packet=answer_packet,
            owner_intelligence_context=owner_intelligence_context,
            league_owner_intelligence_context=league_owner_intelligence_context,
            football_intelligence_context=football_intelligence_context,
        )
        provider_result = self.provider.reason(request)
        if not provider_result.ok or not provider_result.response:
            trace = provider_result.trace or ReasoningTrace(request_id, "failed", eligibility.decision, fallback_status="deterministic_fallback", safe_error_code=provider_result.error_code)
            trace = ReasoningTrace(**{
                **trace.__dict__,
                "fallback_status": "deterministic_fallback",
                "fallback_reason": provider_result.error_code or getattr(trace, "safe_error_code", None) or "provider_result_not_ok",
                "final_answer_source": "deterministic_fallback",
            })
            return ReasonedAnswer(fallback_text, True, provider_result, None, request, trace)
        validation = validate_reasoning_response(request, provider_result.response)
        if not validation.ok:
            trace = provider_result.trace or ReasoningTrace(request_id, "failed", eligibility.decision)
            trace = ReasoningTrace(**{
                **trace.__dict__,
                "validation_status": validation.status,
                "validation_errors": list(validation.errors),
                "fallback_status": "deterministic_fallback",
                "fallback_reason": validation.errors[0] if validation.errors else "validation_failed",
                "final_answer_source": "deterministic_fallback",
                "safe_error_code": validation.errors[0] if validation.errors else "validation_failed",
            })
            return ReasonedAnswer(fallback_text, True, provider_result, validation, request, trace)
        rendered = _compose_response(provider_result.response)
        if not rendered.strip():
            trace = provider_result.trace or ReasoningTrace(request_id, "failed", eligibility.decision)
            trace = ReasoningTrace(**{
                **trace.__dict__,
                "validation_status": validation.status,
                "validation_errors": [],
                "fallback_status": "deterministic_fallback",
                "fallback_reason": "no_renderable_answer",
                "final_answer_source": "deterministic_fallback",
                "safe_error_code": "no_renderable_answer",
            })
            return ReasonedAnswer(fallback_text, True, provider_result, validation, request, trace)
        trace = provider_result.trace or ReasoningTrace(request_id, "success", eligibility.decision)
        trace = ReasoningTrace(**{
            **trace.__dict__,
            "validation_status": validation.status,
            "validation_errors": [],
            "fallback_status": "not_used",
            "fallback_reason": None,
            "final_answer_source": "openai",
        })
        return ReasonedAnswer(rendered, True, provider_result, validation, request, trace)


def determine_reasoning_eligibility(*, question: str, context: Any, answer_packet: AnswerPacket | None, decision_plan: Any, interpreted_question: Any) -> ReasoningEligibility:
    if not context or not getattr(context, "user_id", None) or not getattr(context, "league_id", None) or not getattr(context, "league_team_id", None):
        return ReasoningEligibility("prohibited_invalid_scope", False, "invalid_scope")
    raw = str(question or "").strip().lower().rstrip("?")
    if not raw:
        return ReasoningEligibility("prohibited_empty_question", False, "empty_question")
    mode = getattr(answer_packet, "answer_mode", "")
    if mode in {"blocked", "failed", "unsupported"}:
        return ReasoningEligibility("prohibited_invalid_answer_packet", False, "invalid_answer_packet")
    if any(marker in raw for marker in DIRECT_BYPASS_MARKERS):
        return ReasoningEligibility("deterministic_answer_only", False, "direct_factual_bypass")
    plan_type = str(getattr(decision_plan, "plan_type", "") or "")
    intent = str(getattr(interpreted_question, "primary_intent", "") or "")
    if any(marker in raw for marker in REASONING_MARKERS):
        return ReasoningEligibility("openai_reasoning_recommended", True, "strategic_synthesis")
    if mode in {"recommendation", "comparison", "ranked_options", "conditional_recommendation", "limited_information"}:
        return ReasoningEligibility("openai_explanation_optional", True, "validated_explanation")
    if plan_type in {"trade_evaluation_plan", "long_term_planning_plan", "draft_recommendation_plan", "draft_pick_evaluation_plan", "scenario_simulation_plan"}:
        return ReasoningEligibility("openai_reasoning_recommended", True, "strategic_plan")
    if intent in REASONING_ELIGIBLE_INTENTS:
        return ReasoningEligibility("openai_reasoning_recommended", True, "strategic_intent")
    return ReasoningEligibility("deterministic_answer_only", False, "not_reasoning_eligible")


def _compose_response(response: ReasoningResponse) -> str:
    if response.answer_type == "clarification_required" and response.clarifying_question:
        return response.clarifying_question
    parts = [response.direct_answer.strip()]
    if response.recommendation:
        parts.append(response.recommendation.strip())
    if response.key_reasons:
        parts.append("Why: " + "; ".join(response.key_reasons[:4]))
    if response.main_risks:
        parts.append("Main risk: " + "; ".join(response.main_risks[:3]))
    if response.alternatives:
        alt = response.alternatives[0]
        parts.append(f"Alternative: {alt.description}")
    if response.ranked_players:
        lines = ["Ranking:"]
        for player in sorted(response.ranked_players, key=lambda item: item.rank):
            reason = f" - {player.short_reason.strip()}" if player.short_reason.strip() else ""
            lines.append(f"{player.rank}. {player.player_name}{reason}")
        parts.append("\n".join(lines))
    if response.limitations:
        parts.append("What could change this: " + "; ".join(response.limitations[:3]))
    return "\n\n".join(part for part in parts if part)


def _request_id(context: Any, question: str) -> str:
    raw = f"{getattr(context, 'user_id', '')}:{getattr(context, 'league_id', '')}:{getattr(context, 'league_team_id', '')}:{question}"
    return sha256(raw.encode("utf-8")).hexdigest()[:24]
