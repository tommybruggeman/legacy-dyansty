from __future__ import annotations

import re
import json
from dataclasses import dataclass, field

from gm_assistant.openai_reasoning.models import ReasoningRequest, ReasoningResponse
from gm_assistant.openai_reasoning.prompt_builder import allowed_fact_refs_and_numbers


INVALID_LANGUAGE = (
    "will accept",
    "should accept your offer",
    "guaranteed",
    "lock to happen",
    "api key",
    "supabase credential",
    "system prompt",
    "chain-of-thought",
    "projected points",
    "market value",
    "projects to",
    "projected for",
    "has an injury",
    "is injured",
)


@dataclass(frozen=True)
class ReasoningValidation:
    ok: bool
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_reasoning_response(request: ReasoningRequest, response: ReasoningResponse) -> ReasoningValidation:
    errors: list[str] = []
    warnings: list[str] = []
    if response.answer_type not in {"factual_explanation", "recommendation", "comparison", "scenario_analysis", "clarification_required", "insufficient_evidence", "unsupported"}:
        errors.append("invalid_answer_type")
    if response.recommendation_strength not in {"strong", "moderate", "slight", "none"}:
        errors.append("invalid_recommendation_strength")
    allowed_refs = set(request.allowed_fact_refs)
    unknown = [ref for ref in response.facts_used if ref not in allowed_refs]
    unknown.extend(
        ref
        for item in response.ranked_players
        for ref in item.fact_refs
        if ref not in allowed_refs
    )
    if unknown:
        errors.append("unknown_fact_reference")
    text = _combined_text(response)
    lower = text.lower()
    verified=json.dumps(request.verified_facts,sort_keys=True,default=str).lower()
    if "mixed_season_legality_deferred" in verified:
        forbidden=("is cap legal","is cap illegal","this is cap legal","this is cap illegal","trade is legal","trade is illegal","have enough cap","can afford this","puts you over the cap","remain under the cap")
        if any(x in lower for x in forbidden):errors.append("mixed_season_legality_claim")
        if not response.limitations:errors.append("missing_mixed_season_limitation")
    if '"free_agent_publication_status": "not_published"' in verified and any(x in lower for x in ("is a free agent","published as a free agent","available to sign")):
        errors.append("unsupported_free_agent_publication")
    if "natural expiration" in lower and any(x in lower for x in ("creates dead cap","causes dead cap","dead cap penalty applies")):
        errors.append("invented_natural_expiration_dead_cap")
    if "dropping" in lower and any(x in lower for x in ("cancels the contract","ends the contract","terminates the contract")):
        errors.append("unsupported_drop_termination")
    for marker in INVALID_LANGUAGE:
        if marker in lower:
            errors.append("unsupported_or_sensitive_claim")
            break
    if re.search(r"\b\d{2,3}%", text):
        errors.append("unsupported_percentage")
    supplied_numbers = set(request.validation_constraints.get("authoritative_numbers") or [])
    for number in _numbers(text):
        if number not in supplied_numbers and number not in {"1", "2", "3", "4", "5"}:
            errors.append("unsupported_number")
            break
    if request.permitted_recommendation_scope == "factual_or_limited_summary_only" and response.recommendation:
        errors.append("recommendation_not_permitted")
    if request.permitted_recommendation_scope == "factual_or_limited_summary_only" and response.recommendation_strength != "none":
        errors.append("recommendation_strength_not_permitted")
    if response.answer_type == "clarification_required" and not response.clarifying_question:
        errors.append("missing_clarifying_question")
    return ReasoningValidation(not errors, "approved" if not errors else "rejected", errors, warnings)


def _combined_text(response: ReasoningResponse) -> str:
    parts = [
        response.direct_answer,
        response.recommendation or "",
        response.clarifying_question or "",
        *response.key_reasons,
        *response.main_risks,
        *response.limitations,
        *response.constraint_conflicts,
    ]
    for alt in response.alternatives:
        parts.extend([alt.label, alt.description, alt.strategic_purpose, *alt.required_verified_assets, *alt.unverified_assumptions])
    for player in response.ranked_players:
        parts.extend([str(player.rank), player.player_name, player.player_id, player.short_reason, *player.fact_refs])
    return " ".join(parts)


def _numbers(text: str) -> set[str]:
    out = set()
    for raw in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", text):
        try:
            number = float(raw)
        except Exception:
            continue
        if number.is_integer():
            out.add(str(int(number)))
        else:
            out.add(str(round(number, 4)).rstrip("0").rstrip("."))
    return out
