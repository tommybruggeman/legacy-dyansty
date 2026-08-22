from __future__ import annotations

import os
import time
from pathlib import Path

from gm_assistant.evaluation.cases import initial_evaluation_cases
from gm_assistant.evaluation.models import EvaluationCase, EvaluationResult, EvaluationSuiteResult
from gm_assistant.evaluation.scoring import comparison_label, score_evaluation_case
from gm_assistant.openai_reasoning import (
    OpenAIReasoningProvider,
    ProviderResult,
    ReasoningProvider,
    ReasoningRequest,
    UnavailableReasoningProvider,
    configuration_status,
    validate_reasoning_response,
)


SYNTHETIC_LEAGUE_ID = "synthetic-league"
SYNTHETIC_LEAGUE_TEAM_ID = "synthetic-team"


def live_evaluation_enabled() -> bool:
    status = configuration_status()
    return status.live_testing_permitted and status.configuration_valid


def synthetic_qb_shortage_request() -> ReasoningRequest:
    return _reasoning_request_for_case(
        EvaluationCase(
            case_id="synthetic_qb_shortage_trade",
            category="trade_reasoning",
            user_question="Should I trade my only backup quarterback with a protected 2028 first?",
            expected_answer_type="recommendation",
            required_facts=("football.needs.immediate_starter_shortage.v1", "owner.goal"),
            forbidden_claims=("will accept", "market value", "api key"),
            openai_eligible=True,
        )
    )


def run_evaluation_suite(
    *,
    provider: ReasoningProvider | None = None,
    cases: tuple[EvaluationCase, ...] | None = None,
    live: bool = False,
) -> EvaluationSuiteResult:
    selected_cases = cases or initial_evaluation_cases()
    active_provider = provider or (OpenAIReasoningProvider.from_environment() if live else UnavailableReasoningProvider("live_not_enabled"))
    results = tuple(_run_case(case, active_provider) for case in selected_cases)
    return EvaluationSuiteResult(
        suite_id="legacy_openai_stage10_initial_v1",
        results=results,
        live=live,
    )


def render_markdown_report(suite: EvaluationSuiteResult) -> str:
    lines = [
        "# Legacy GM Assistant OpenAI Evaluation",
        "",
        f"- Suite: {suite.suite_id}",
        f"- Live provider: {suite.live}",
        f"- Cases: {suite.total_cases}",
        f"- Passed: {suite.passed}",
        f"- Failed: {suite.failed}",
        f"- Improved: {suite.improved}",
        f"- Neutral: {suite.neutral}",
        f"- Regressed: {suite.regressed}",
        f"- Total tokens: {suite.total_tokens}",
        "",
        "## Case Results",
    ]
    for result in suite.results:
        status = "PASS" if result.score and result.score.passed else "FAIL"
        failures = ", ".join(result.score.failures if result.score else ("missing_score",)) or "none"
        lines.extend(
            [
                "",
                f"### {result.case.case_id}",
                f"- Status: {status}",
                f"- Category: {result.case.category}",
                f"- Provider called: {result.provider_called}",
                f"- Calls: {result.provider_call_count}",
                f"- Validation: {result.validation_status or 'not_applicable'}",
                f"- Fallback: {result.fallback_status or 'not_applicable'}",
                f"- Comparison: {result.comparison}",
                f"- Failures: {failures}",
                f"- Reviewer notes: {result.reviewer_notes or 'Review sampled answer quality before enabling broadly.'}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def write_markdown_report(suite: EvaluationSuiteResult, path: str | Path) -> Path:
    target = Path(path)
    target.write_text(render_markdown_report(suite), encoding="utf-8")
    return target


def _run_case(case: EvaluationCase, provider: ReasoningProvider) -> EvaluationResult:
    fallback = _deterministic_fallback_for_case(case)
    start = time.perf_counter()
    provider_result: ProviderResult | None = None
    validation_status = None
    fallback_status = "deterministic_fallback"
    rendered = fallback

    if case.openai_eligible:
        request = _reasoning_request_for_case(case)
        provider_result = provider.reason(request)
        if provider_result.ok and provider_result.response:
            validation = validate_reasoning_response(request, provider_result.response)
            validation_status = validation.status
            if validation.ok:
                rendered = provider_result.response.direct_answer
                fallback_status = "openai_reasoning_used"
    latency_ms = int((time.perf_counter() - start) * 1000)
    trace = provider_result.trace if provider_result else None
    provider_called = provider_result is not None
    score = score_evaluation_case(
        case,
        rendered_answer=rendered,
        provider_called=provider_called,
        validation_status=validation_status,
        fallback_status=fallback_status,
    )
    return EvaluationResult(
        case=case,
        deterministic_fallback=fallback,
        rendered_answer=rendered,
        provider_called=provider_called,
        provider_call_count=1 if provider_called else 0,
        response_type=getattr(getattr(provider_result, "response", None), "answer_type", None),
        validation_status=validation_status,
        fallback_status=fallback_status,
        latency_ms=latency_ms,
        input_tokens=getattr(trace, "input_tokens", None),
        output_tokens=getattr(trace, "output_tokens", None),
        total_tokens=getattr(trace, "total_tokens", None),
        comparison=comparison_label(score, provider_called=provider_called),
        score=score,
        reviewer_notes=_reviewer_notes(case, provider_called, fallback_status),
    )


def _reasoning_request_for_case(case: EvaluationCase) -> ReasoningRequest:
    allowed = sorted(set(case.required_facts + ("answer.direct_answer", "validation.status", "owner.goal")))
    return ReasoningRequest(
        request_id=f"eval-{case.case_id}",
        league_id=SYNTHETIC_LEAGUE_ID,
        league_team_id=SYNTHETIC_LEAGUE_TEAM_ID,
        normalized_intent=case.category,
        normalized_objective=case.expected_answer_type,
        user_question=case.user_question,
        verified_facts={
            "answer": {"direct_answer": _deterministic_fallback_for_case(case)},
            "owner": {"goal": "balanced contender with future-first protection"},
            "football": {
                "needs": {
                    "immediate_starter_shortage": "QB depth shortage if backup is traded",
                    "immediate_starter_shortage_rule": "football.needs.immediate_starter_shortage.v1",
                }
            },
            "draft": {"pick_context": "2028 first is protected in this synthetic fixture"},
        },
        deterministic_calculations={"cap": {"available_cap": "synthetic"}},
        validation_constraints={
            "authoritative_numbers": ["2028", "1", "2", "3", "4", "5"],
            "forbidden_claims": list(case.forbidden_claims),
        },
        known_missing_evidence=[] if case.expected_answer_type != "insufficient_evidence" else ["counterparty_acceptance"],
        permitted_recommendation_scope="explain_validated_recommendation_only",
        allowed_fact_refs=allowed,
        safe_lineage_refs=allowed,
    )


def _deterministic_fallback_for_case(case: EvaluationCase) -> str:
    if case.category == "direct_factual_bypass":
        return "Verified factual answer from the scoped deterministic assistant context."
    if case.expected_answer_type == "insufficient_evidence":
        return "I do not have enough verified evidence to answer that safely."
    if case.category == "safety":
        return "I cannot help with that request, but I can answer from verified league facts."
    return "The deterministic fallback is to explain only verified facts and avoid unsupported claims."


def _reviewer_notes(case: EvaluationCase, provider_called: bool, fallback_status: str) -> str:
    if os.getenv("OPENAI_API_KEY"):
        return "Local key was present; report intentionally does not include it."
    if provider_called and fallback_status == "openai_reasoning_used":
        return "Provider answer passed deterministic validation; human review should sample clarity and usefulness."
    if not case.openai_eligible:
        return "Direct factual bypass remained deterministic."
    return "Provider was unavailable or rejected; deterministic fallback preserved a safe answer."
