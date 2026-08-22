from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from gm_assistant.answer_packet import AnswerPacket
from gm_assistant.assistant_pipeline import run_assistant_pipeline
from gm_assistant.brain_context import AssistantAccessError, AssistantIdentity
from gm_assistant.conversation_state import ConversationState
from gm_assistant.evidence import EvidenceRetrievalProvider, SupabaseEvidenceRetrievalProvider
from gm_assistant.openai_service import (
    AssistantAnswer,
    AssistantConfigurationError,
    AssistantServiceError,
    answer_gm_question,
)
from gm_assistant.request_context import AssistantRequestContext


LOGGER = logging.getLogger(__name__)


RuntimeAnswerer = Callable[..., AssistantAnswer]
RetrievalProviderFactory = Callable[[Any], EvidenceRetrievalProvider]


@dataclass(frozen=True)
class AssistantRuntimeInput:
    context: AssistantRequestContext
    question: str
    conversation_state: ConversationState | None = None
    conversation_history: Sequence[Mapping[str, str]] | None = None
    owner_preferences: Mapping[str, Any] | None = None
    team_context: Mapping[str, Any] | None = None
    supabase_client: Any | None = None
    message_id: str | None = None
    reasoning_provider: Any | None = None


@dataclass(frozen=True)
class AssistantRuntimeResult:
    ok: bool
    answer_text: str
    conversation_state: ConversationState | None = None
    assistant_answer: AssistantAnswer | None = None
    answer_packet: AnswerPacket | None = None
    validation_status: str | None = None
    rendered_validation_status: str | None = None
    prompt_size_audit: dict[str, int] | None = None
    evidence_diagnostics: dict[str, Any] | None = None
    error_code: str | None = None

    @property
    def safe_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {"ok": self.ok}
        if self.error_code:
            metadata["error_code"] = self.error_code
        if self.validation_status:
            metadata["validation_status"] = self.validation_status
        if self.rendered_validation_status:
            metadata["rendered_validation_status"] = self.rendered_validation_status
        if self.evidence_diagnostics:
            metadata["evidence"] = dict(self.evidence_diagnostics)
        if self.assistant_answer:
            metadata["model"] = self.assistant_answer.model
            metadata["latency_ms"] = self.assistant_answer.latency_ms
            metadata["tool_calls"] = list(self.assistant_answer.tool_calls)
            trace = getattr(self.assistant_answer, "reasoning_trace", None)
            if trace:
                payload = trace.to_payload() if hasattr(trace, "to_payload") else dict(trace)
                metadata["reasoning"] = {
                    key: payload.get(key)
                    for key in (
                        "provider_selected",
                        "provider_called",
                        "provider_skipped_reason",
                        "provider_status",
                        "eligibility_decision",
                        "result_status",
                        "schema_parse_status",
                        "validation_status",
                        "validation_errors",
                        "fallback_status",
                        "fallback_reason",
                        "final_answer_source",
                        "safe_error_code",
                        "latency_ms",
                        "input_tokens",
                        "output_tokens",
                        "total_tokens",
                    )
                }
        return metadata


class AssistantRuntime:
    """Canonical orchestration facade for one GM Assistant turn.

    The runtime coordinates the existing scoped Stage 1-13 pipeline and final
    rendered-answer validation. It intentionally has no Streamlit dependency.
    """

    def __init__(
        self,
        *,
        answerer: RuntimeAnswerer = answer_gm_question,
        retrieval_provider_factory: RetrievalProviderFactory = SupabaseEvidenceRetrievalProvider,
    ) -> None:
        self._answerer = answerer
        self._retrieval_provider_factory = retrieval_provider_factory

    def run(self, request: AssistantRuntimeInput) -> AssistantRuntimeResult:
        if not request.context or not request.context.user_id or not request.context.league_id:
            return self._failure("identity_context_missing")
        if not request.context.league_team_id:
            return self._failure("team_context_missing")
        if not request.question or not request.question.strip():
            return self._failure("unsupported_request", "Please ask a GM question first.")

        try:
            provider = self._retrieval_provider_factory(request.supabase_client)
            context = (
                replace(request.context, message_id=request.message_id)
                if request.message_id
                else request.context
            )
            pipeline_result = run_assistant_pipeline(
                context=context,
                question=request.question,
                conversation_state=request.conversation_state,
                retrieval_provider=provider,
                owner_preferences=dict(request.owner_preferences or {}),
                team_context=dict(request.team_context or {}),
                interpreter_sb=request.supabase_client,
            )
            answer = self._answerer(
                question=request.question,
                identity=_identity_from_context(context),
                conversation_history=[dict(item) for item in request.conversation_history or []],
                conversation_state=pipeline_result.conversation_state,
                interpreted_question=pipeline_result.interpreted_question,
                owner_objective=pipeline_result.owner_objective,
                decision_plan=pipeline_result.decision_plan,
                evidence_packet=pipeline_result.evidence_packet,
                rules_evaluation=pipeline_result.rules_evaluation,
                calculation_packet=pipeline_result.calculation_packet,
                decision_output=pipeline_result.decision_output,
                recommendation_validation=pipeline_result.recommendation_validation,
                answer_packet=pipeline_result.answer_packet,
                request_context=context,
                owner_intelligence_context=getattr(pipeline_result, "owner_intelligence_context", None),
                league_owner_intelligence_context=getattr(pipeline_result, "league_owner_intelligence_context", None),
                football_intelligence_context=getattr(pipeline_result, "football_intelligence_context", None),
                reasoning_provider=request.reasoning_provider,
                sb=request.supabase_client,
            )
            return AssistantRuntimeResult(
                ok=True,
                answer_text=answer.text,
                conversation_state=pipeline_result.conversation_state,
                assistant_answer=answer,
                answer_packet=pipeline_result.answer_packet,
                validation_status=pipeline_result.recommendation_validation.validation_status,
                rendered_validation_status=(
                    answer.rendered_validation.validation_status
                    if answer.rendered_validation
                    else pipeline_result.rendered_validation.validation_status
                ),
                prompt_size_audit=dict(pipeline_result.prompt_size_audit),
                evidence_diagnostics=_evidence_diagnostics(pipeline_result.evidence_packet),
            )
        except AssistantAccessError:
            LOGGER.info("GM Assistant runtime blocked by assistant access validation.")
            return self._failure("identity_context_invalid")
        except AssistantConfigurationError:
            LOGGER.info("GM Assistant runtime is missing local answer rendering configuration.")
            return self._failure("rendering_configuration_missing")
        except AssistantServiceError:
            LOGGER.info("GM Assistant runtime answer service failed safely.")
            return self._failure("answer_service_unavailable")
        except Exception:
            LOGGER.exception("Unexpected GM Assistant runtime failure.")
            return self._failure("unexpected_internal_failure")

    @staticmethod
    def _failure(error_code: str, message: str | None = None) -> AssistantRuntimeResult:
        return AssistantRuntimeResult(
            ok=False,
            answer_text=message or _SAFE_FAILURE_MESSAGES.get(error_code, _SAFE_FAILURE_MESSAGES["unexpected_internal_failure"]),
            error_code=error_code,
        )


def _identity_from_context(context: AssistantRequestContext) -> AssistantIdentity:
    return AssistantIdentity(
        team_name=context.team_name or context.owner_name or "Active team",
        user_id=context.user_id,
        league_id=context.league_id,
        league_team_id=context.league_team_id,
        allow_legacy_fallback=False,
    )


def _evidence_diagnostics(evidence_packet: Any) -> dict[str, Any]:
    evaluations = list(getattr(evidence_packet, "player_evaluation_evidence", []) or [])
    retrievals = list(getattr(evidence_packet, "retrieval_results", []) or [])
    roster_count = 0
    missing_contract_count = 0
    missing_profile_count = 0
    for item in evaluations:
        missing = set(getattr(item, "missing_inputs", []) or [])
        if "contract_efficiency_score" in missing:
            missing_contract_count += 1
        if {"current_contribution_score", "future_outlook_score", "league_relative_score"} & missing:
            missing_profile_count += 1
    requested = any(getattr(item, "retrieval_type", None) == "player_evaluations" for item in retrievals)
    fact_ref_count = len({ref for item in evaluations for ref in (getattr(item, "fact_refs", []) or [])})
    insufficient = [item.player_name for item in evaluations if getattr(item, "status", "") == "insufficient_data"]
    correlations = _player_evaluation_correlations(evaluations)
    if requested:
        roster_count = max((getattr(item, "record_count", 0) for item in retrievals if getattr(item, "retrieval_type", None) in {"team_roster", "player_evaluations"}), default=len(evaluations))
    return {
        "roster_player_count": roster_count,
        "evaluated_player_count": len(evaluations),
        "missing_profile_count": missing_profile_count,
        "missing_contract_count": missing_contract_count,
        "player_evaluation_requested": requested,
        "player_evaluation_included": bool(evaluations),
        "player_evaluation_fact_ref_count": fact_ref_count,
        "players_excluded_for_insufficient_data": insufficient,
        "player_evaluation_component_correlations": correlations,
        "rookie_prospect_pathway_count": sum(1 for item in evaluations if getattr(item, "rookie_prospect_pathway_used", False)),
        "positional_adjustment_applied_count": sum(1 for item in evaluations if getattr(item, "positional_adjustment_applied", False)),
    }


def _player_evaluation_correlations(evaluations: list[Any]) -> dict[str, float]:
    pairs = {
        "current_future": ("current_contribution_score", "future_outlook_score"),
        "current_relative": ("current_contribution_score", "league_relative_score"),
        "future_relative": ("future_outlook_score", "league_relative_score"),
    }
    return {
        label: value
        for label, fields in pairs.items()
        if (value := _pearson(evaluations, fields[0], fields[1])) is not None
    }


def _pearson(items: list[Any], left: str, right: str) -> float | None:
    values = [
        (float(getattr(item, left)), float(getattr(item, right)))
        for item in items
        if getattr(item, left, None) is not None and getattr(item, right, None) is not None
    ]
    if len(values) < 2:
        return None
    xs = [item[0] for item in values]
    ys = [item[1] for item in values]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in values)
    denom_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    denom_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if denom_x == 0 or denom_y == 0:
        return None
    return round(numerator / (denom_x * denom_y), 3)


_SAFE_FAILURE_MESSAGES = {
    "identity_context_missing": (
        "I could not reconnect your assistant context to your account. "
        "Please reload the page and try again."
    ),
    "team_context_missing": (
        "I could not reconnect your assistant context to a league team. "
        "Please reload the page or ask the commissioner to verify your membership."
    ),
    "identity_context_invalid": (
        "I could not reconnect your assistant context to a league team. "
        "Please reload the page or ask the commissioner to verify your membership."
    ),
    "unsupported_request": "Please ask a GM question first.",
    "missing_evidence": "I could not verify enough league data to answer that safely yet.",
    "validation_rejected": "I could not validate a safe answer from the available league data.",
    "rendering_configuration_missing": (
        "Coach Condor is ready, but OpenAI is not configured yet. "
        "Add `OPENAI_API_KEY` locally, then reload the page."
    ),
    "answer_service_unavailable": "Coach Condor is temporarily unavailable. Please try again in a moment.",
    "unexpected_internal_failure": "Coach Condor hit an unexpected local error. Please reload the GM context and try again.",
}
