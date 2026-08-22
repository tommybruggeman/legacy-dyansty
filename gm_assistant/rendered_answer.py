from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable

from gm_assistant.answer_packet import AnswerPacket


RENDERED_VALIDATION_VERSION = "gm_rendered_answer_validation.v1"
FALLBACK_RENDERER_VERSION = "deterministic_fallback_renderer_stage12.v1"

MAX_CHECKS = 24
MAX_VIOLATIONS = 12
MAX_WARNINGS = 8
MAX_TEXT_CHARS = 3500


class RenderedValidationStatus(str, Enum):
    APPROVED = "approved"
    APPROVED_WITH_WARNINGS = "approved_with_warnings"
    FALLBACK_USED = "fallback_used"
    BLOCKED = "blocked"
    FAILED = "failed"


class RenderedCheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    NOT_APPLICABLE = "not_applicable"
    UNVERIFIABLE = "unverifiable"


class RenderedSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class RenderedAnswerCheck:
    check_type: str
    status: str
    required: bool
    explanation: str
    related_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RenderedAnswerViolation:
    violation_type: str
    severity: str
    explanation: str
    blocking: bool
    related_refs: list[str] = field(default_factory=list)
    detected_text: str | None = None


@dataclass(frozen=True)
class RenderedAnswerWarning:
    warning_type: str
    explanation: str
    related_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RenderedAnswerValidation:
    validation_status: str
    original_rendered_text: str | None
    approved_text: str
    used_openai_response: bool
    used_deterministic_fallback: bool
    checks: list[RenderedAnswerCheck] = field(default_factory=list)
    violations: list[RenderedAnswerViolation] = field(default_factory=list)
    warnings: list[RenderedAnswerWarning] = field(default_factory=list)
    preserved_claim_refs: list[str] = field(default_factory=list)
    missing_claim_refs: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    answer_packet_version: str | None = None
    renderer_validation_version: str = RENDERED_VALIDATION_VERSION
    fallback_renderer_version: str = FALLBACK_RENDERER_VERSION

    def to_payload(self) -> dict[str, Any]:
        return _compact(asdict(self))


@dataclass
class _RenderedWork:
    checks: list[RenderedAnswerCheck] = field(default_factory=list)
    violations: list[RenderedAnswerViolation] = field(default_factory=list)
    warnings: list[RenderedAnswerWarning] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    def check(self, check_type: str, status: str, required: bool, explanation: str, refs: list[str] | None = None) -> None:
        self.checks.append(RenderedAnswerCheck(check_type, status, required, explanation, _dedupe(refs or [])))

    def violation(self, violation_type: str, explanation: str, *, blocking: bool = True, refs: list[str] | None = None, detected: str | None = None) -> None:
        severity = RenderedSeverity.BLOCKING.value if blocking else RenderedSeverity.ERROR.value
        self.violations.append(RenderedAnswerViolation(violation_type, severity, explanation, blocking, _dedupe(refs or []), detected))

    def warn(self, warning_type: str, explanation: str, refs: list[str] | None = None) -> None:
        self.warnings.append(RenderedAnswerWarning(warning_type, explanation, _dedupe(refs or [])))


RenderedCheck = Callable[[AnswerPacket, str | None, _RenderedWork], None]


def validate_rendered_answer(answer_packet: AnswerPacket, rendered_text: str | None) -> RenderedAnswerValidation:
    fallback = render_answer_packet_fallback(answer_packet)
    work = _RenderedWork()
    try:
        text = _display_text(rendered_text)
        for check in RENDERED_ANSWER_CHECKS:
            check(answer_packet, text, work)
        blocking = any(item.blocking for item in work.violations)
        if blocking:
            return _validation_result(
                answer_packet,
                rendered_text,
                fallback,
                RenderedValidationStatus.FALLBACK_USED.value,
                False,
                True,
                work,
            )
        status = RenderedValidationStatus.APPROVED_WITH_WARNINGS.value if work.warnings else RenderedValidationStatus.APPROVED.value
        return _validation_result(answer_packet, rendered_text, text or fallback, status, True, False, work)
    except Exception:
        work.check("validator_runtime", RenderedCheckStatus.FAILED.value, True, "Rendered answer validation failed safely.", ["rendered_answer"])
        work.violation("validator_runtime", "Rendered answer validation failed safely.", refs=["rendered_answer"])
        return _validation_result(
            answer_packet,
            rendered_text,
            fallback,
            RenderedValidationStatus.FAILED.value,
            False,
            True,
            work,
        )


def render_answer_packet_fallback(answer_packet: AnswerPacket) -> str:
    mode = answer_packet.answer_mode
    lines: list[str] = []
    direct = answer_packet.direct_answer.text if answer_packet.direct_answer else None
    if mode == "clarification_required":
        return _bounded_text(direct or "Which specific player, team, pick, or terms do you mean?")
    if mode == "unsupported":
        lines.append(direct or "I cannot support that request with the authorized assistant tools.")
    elif mode == "blocked":
        lines.append(direct or "I cannot safely answer this from the current scoped data.")
    elif mode == "failed":
        lines.append(direct or "I cannot safely assemble an answer right now.")
    elif mode in {"direct_fact", "direct_rules", "limited_information"}:
        lines.append(direct or "I can only give a limited answer from the verified data.")
    elif answer_packet.recommendation:
        rec = answer_packet.recommendation
        lines.append(f"My recommendation: **{_label_action(rec.action)} {rec.label}**.")
        if rec.actionable_now and answer_packet.approved_for_action:
            lines.append("This recommendation is actionable under the verified league information.")
        else:
            lines.append("This is not ready to act on yet.")
    elif direct:
        lines.append(direct)
    else:
        lines.append("I can only give a limited answer from the verified data.")

    if mode in {"direct_fact", "direct_rules"}:
        return _bounded_text("\n".join(lines))
    if mode == "limited_information" and direct:
        return _bounded_text("\n".join(lines))

    reasons = answer_packet.reasons[:4]
    facts = answer_packet.facts[:3]
    calculations = answer_packet.calculations[:3]
    conditions = [item for item in answer_packet.conditions if item.blocking][:3]
    limitations = answer_packet.limitations[:3]
    alternatives = answer_packet.alternatives[:3]

    detail_lines = []
    for reason in reasons:
        detail_lines.append(reason.explanation)
    for fact in facts:
        detail_lines.append(f"{fact.label}: {_format_value(fact.value)}")
    for calc in calculations:
        label = "estimated " if calc.estimated else ""
        detail_lines.append(f"{calc.label}: {label}{_format_value(calc.value)}")
    if detail_lines:
        lines.append("")
        lines.append("Why:")
        lines.extend(f"- {item}" for item in detail_lines[:5])
    if conditions:
        lines.append("")
        lines.append("Conditions:")
        lines.extend(f"- {item.explanation}" for item in conditions)
    if limitations:
        lines.append("")
        lines.append("Limitations:")
        lines.extend(f"- {item.explanation}" for item in limitations)
    if alternatives:
        lines.append("")
        lines.append("Alternatives:")
        lines.extend(f"- {_label_action(item.action)} {item.label}" for item in alternatives)
    return _bounded_text("\n".join(lines))


def build_rendered_answer_validation_payload(validation: RenderedAnswerValidation | None) -> dict[str, Any]:
    if not validation:
        return {}
    return validation.to_payload()


def _validate_response_presence(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    if not text or not text.strip():
        work.check("response_presence", RenderedCheckStatus.FAILED.value, True, "Rendered response is empty.", ["rendered_text"])
        work.violation("empty_response", "OpenAI returned no displayable answer.", refs=["rendered_text"])
        return
    normalized = _normalize(text)
    placeholders = {"error", "none", "null", "{}", "[]", "assistant_error", "internal_metadata"}
    if normalized in placeholders or normalized.startswith(("structured deterministic", "answer_mode", "validation_status")):
        work.violation("metadata_only_response", "Rendered response contains only metadata or placeholder content.", refs=["rendered_text"], detected=text[:120])
    if len(normalized) < 8 and packet.direct_answer:
        work.violation("truncated_response", "Rendered response is too short to communicate the direct answer.", refs=["direct_answer"], detected=text)
    work.check("response_presence", RenderedCheckStatus.PASSED.value, True, "Rendered response is present.", ["rendered_text"])


def _validate_response_mode(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    if not text:
        return
    norm = _normalize(text)
    if packet.answer_mode == "limited_information" and _contains_action_recommendation(norm):
        work.violation("limited_mode_recommendation", "Limited-information answers must not invent a strategic recommendation.", refs=["answer_mode"], detected=_snippet(text, norm))
    if packet.answer_mode == "unsupported" and _contains_action_recommendation(norm):
        work.violation("unsupported_mode_solution", "Unsupported answers must not attempt to solve the request.", refs=["answer_mode"], detected=_snippet(text, norm))
    if packet.answer_mode == "blocked" and _contains_action_recommendation(norm):
        work.violation("blocked_mode_solution", "Blocked answers must not introduce a fantasy action.", refs=["answer_mode"], detected=_snippet(text, norm))
    work.check("response_mode", RenderedCheckStatus.PASSED.value, True, "Answer mode constraints checked.", ["answer_mode"])


def _validate_direct_answer(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    if not packet.direct_answer or not text:
        work.check("direct_answer", RenderedCheckStatus.NOT_APPLICABLE.value, False, "No direct answer required.", [])
        return
    required = _keywords(packet.direct_answer.text)
    norm = _normalize(text)
    missing = [word for word in required if word not in norm]
    if missing and packet.answer_mode not in {"recommendation", "conditional_recommendation", "ranked_options", "clarification_required"}:
        work.missing.append("direct_answer")
        work.violation("direct_answer_omitted", "Rendered response omits the core direct answer.", refs=packet.direct_answer.source_refs, detected=text[:160])
    else:
        work.preserved.extend(packet.direct_answer.source_refs)
    if _opposes_claim(packet.direct_answer.text, text):
        work.violation("direct_answer_reversed", "Rendered response appears to reverse the approved direct answer.", refs=packet.direct_answer.source_refs, detected=text[:160])
    work.check("direct_answer", RenderedCheckStatus.PASSED.value, True, "Direct answer preservation checked.", packet.direct_answer.source_refs)


def _validate_recommendation(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    if not packet.recommendation:
        if _contains_action_recommendation(norm):
            work.unsupported.append("invented_recommendation")
            work.violation("invented_recommendation", "Rendered response introduces a recommendation that was not approved.", refs=["recommendation"], detected=text[:180] if text else None)
        work.check("recommendation", RenderedCheckStatus.NOT_APPLICABLE.value, True, "No approved recommendation exists.", ["recommendation"])
        return
    action = packet.recommendation.action
    if not _action_present(action, norm):
        work.missing.append("recommendation_action")
        work.violation("recommendation_action_missing", "Rendered response does not preserve the approved recommendation action.", refs=packet.recommendation.source_refs, detected=text[:180] if text else None)
    opposite = _opposite_action(action)
    if opposite and _action_present(opposite, norm):
        work.violation("recommendation_reversed", "Rendered response reverses the approved recommendation action.", refs=packet.recommendation.source_refs, detected=text[:180] if text else None)
    conflicting_actions = [
        other
        for other in ACTION_SYNONYMS
        if other != action and other != opposite and _action_present(other, norm)
    ]
    if conflicting_actions and action in {"accept", "reject", "acquire", "monitor", "start", "bench", "release", "extend"}:
        work.violation("recommendation_action_changed", "Rendered response introduces a different primary action than the approved recommendation.", refs=packet.recommendation.source_refs, detected=", ".join(conflicting_actions[:3]))
    label_keywords = _keywords(packet.recommendation.label)
    if label_keywords and not any(word in norm for word in label_keywords):
        work.violation("recommendation_target_missing", "Rendered response omits or replaces the approved recommendation target.", refs=packet.recommendation.related_entity_ids, detected=text[:180] if text else None)
    work.preserved.extend(packet.recommendation.source_refs)
    work.check("recommendation", RenderedCheckStatus.PASSED.value, True, "Recommendation preservation checked.", packet.recommendation.source_refs)


def _validate_legality(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    rule_statuses = {item.status for item in packet.rule_conclusions}
    if "violated" in rule_statuses or any("illegal" in _normalize(item.explanation) for item in packet.rule_conclusions):
        if _has_legal_claim(norm):
            work.violation("illegal_changed_to_legal", "Rendered response calls an illegal move legal.", refs=["rules"], detected=text[:180] if text else None)
    if "conditional" in rule_statuses or packet.response_status == "ready_with_conditions":
        if _has_confirmed_legal_claim(norm):
            work.violation("conditional_changed_to_confirmed", "Rendered response turns conditional legality into confirmed legality.", refs=["rules", "conditions"], detected=text[:180] if text else None)
    if any(item.status == "unresolved" for item in packet.rule_conclusions):
        if _has_confirmed_legal_claim(norm):
            work.violation("unverifiable_changed_to_confirmed", "Rendered response confirms legality despite unresolved rules.", refs=["rules"], detected=text[:180] if text else None)
    work.check("legality", RenderedCheckStatus.PASSED.value, True, "Legality language checked.", ["rules"])


def _validate_actionability(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    if not packet.approved_for_action and _has_actionability_claim(norm):
        work.violation("unsupported_actionability", "Rendered response says the recommendation is ready to execute despite action approval being false.", refs=["approved_for_action"], detected=text[:180] if text else None)
    work.check("actionability", RenderedCheckStatus.PASSED.value, True, "Actionability language checked.", ["approved_for_action"])


def _validate_conditions(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    missing = []
    for condition in packet.conditions:
        if not condition.blocking:
            if condition.explanation and not _concept_present(condition.explanation, norm):
                work.warn("optional_condition_omitted", "Optional condition was omitted.", [condition.condition_id])
            continue
        if not _concept_present(condition.explanation, norm):
            missing.append(condition.condition_id)
    if missing:
        work.missing.extend(missing)
        work.violation("blocking_condition_omitted", "Rendered response omits a material blocking condition.", refs=missing, detected=text[:180] if text else None)
    work.check("conditions", RenderedCheckStatus.PASSED.value, True, "Material condition preservation checked.", ["conditions"])


def _validate_numbers(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    if not text:
        return
    approved_numbers = _approved_numbers(packet)
    rendered_numbers = _numbers(text)
    if not rendered_numbers:
        work.check("numbers", RenderedCheckStatus.PASSED.value, False, "No rendered numbers to validate.", [])
        return
    unexpected = []
    for number in rendered_numbers:
        if number in {1, 2, 3, 4, 5}:
            continue
        if approved_numbers and not any(_numbers_equivalent(number, allowed) for allowed in approved_numbers):
            unexpected.append(number)
    if unexpected:
        work.violation("numeric_mismatch", "Rendered response includes a material number not supported by the answer packet.", refs=["calculations", "facts"], detected=", ".join(str(item) for item in unexpected[:4]))
    work.check("numbers", RenderedCheckStatus.PASSED.value, True, "Numeric fidelity checked.", ["facts", "calculations"])


def _validate_estimate_labels(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    has_estimate = any(item.estimated for item in packet.calculations)
    if has_estimate and any(word in norm for word in {"exact", "precise", "confirmed"}) and not _has_estimate_label(norm):
        work.violation("estimate_called_exact", "Rendered response describes an estimated value as exact.", refs=["calculations"], detected=text[:180] if text else None)
    if has_estimate and packet.recommendation and not _has_estimate_label(norm):
        work.violation("estimate_unlabeled", "Rendered response omits estimate labeling for material estimated support.", blocking=True, refs=["calculations"], detected=text[:180] if text else None)
    work.check("estimate_labels", RenderedCheckStatus.PASSED.value, True, "Estimate labels checked.", ["calculations"])


def _validate_forbidden_claims(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    for claim in packet.forbidden_claims:
        if _forbidden_detected(claim.claim_type, norm):
            work.violation(claim.claim_type, claim.explanation, refs=claim.related_refs, detected=text[:180] if text else None)
    if "fabricated projection" in norm or "external projection" in norm:
        work.violation("fabricated_projection", "Rendered response cites unsupported or fabricated projections.", refs=["forbidden_claims"], detected=text[:180] if text else None)
    if "unsupported injury" in norm or "confirmed injured" in norm:
        work.violation("unsupported_injury", "Rendered response makes an unsupported injury claim.", refs=["forbidden_claims"], detected=text[:180] if text else None)
    if "available to add" in norm or "can be acquired" in norm:
        work.violation("false_availability", "Rendered response makes an unsupported availability claim.", refs=["forbidden_claims"], detected=text[:180] if text else None)
    if "exact slot" in norm or "locked in at" in norm:
        work.violation("exact_future_pick_slot", "Rendered response makes an unsupported exact future pick claim.", refs=["forbidden_claims"], detected=text[:180] if text else None)
    work.check("forbidden_claims", RenderedCheckStatus.PASSED.value, True, "Forbidden claims enforced.", ["forbidden_claims"])


def _validate_entity_scope(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    if packet.answer_mode == "clarification_required":
        work.check("entity_scope", RenderedCheckStatus.PASSED.value, True, "Clarification entity wording checked by clarification validation.", ["source_index"])
        return
    norm = _normalize(text or "")
    supported = _supported_entity_terms(packet)
    detected = _capitalized_terms(text or "")
    unsupported = []
    for term in detected:
        normalized = _normalize(term)
        if len(normalized) < 4 or normalized in GENERIC_ENTITY_TERMS:
            continue
        if not any(normalized in item or item in normalized for item in supported):
            unsupported.append(term)
    if unsupported and packet.answer_mode not in {"general_conversation", "failed"}:
        work.unsupported.extend(unsupported[:3])
        work.violation("unsupported_entity", "Rendered response introduces an unsupported specific entity.", refs=["source_index"], detected=", ".join(unsupported[:3]))
    work.check("entity_scope", RenderedCheckStatus.PASSED.value, True, "Specific entity scope checked.", ["source_index"])


def _validate_limitations(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    missing = []
    for limitation in packet.limitations:
        if limitation.limitation_type == "validation_not_approved" and "cannot validate" in norm:
            continue
        material = limitation.effect_on_answer and not limitation.effect_on_answer.lower().startswith("minor")
        if material and not _concept_present(limitation.explanation, norm):
            missing.append(limitation.limitation_type)
    if missing:
        work.missing.extend(missing)
        work.violation("material_limitation_omitted", "Rendered response omits a material limitation.", refs=missing, detected=text[:180] if text else None)
    work.check("limitations", RenderedCheckStatus.PASSED.value, True, "Material limitations checked.", ["limitations"])


def _validate_clarification(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    if packet.answer_mode != "clarification_required" or not packet.direct_answer:
        work.check("clarification", RenderedCheckStatus.NOT_APPLICABLE.value, False, "Clarification is not required.", [])
        return
    question_count = (text or "").count("?")
    if question_count != 1:
        work.violation("clarification_question_count", "Clarification mode must ask exactly one question.", refs=["direct_answer"], detected=text[:180] if text else None)
    if not _concept_present(packet.direct_answer.text, _normalize(text or "")):
        work.violation("wrong_clarification", "Rendered response does not preserve the approved clarification question.", refs=packet.direct_answer.source_refs, detected=text[:180] if text else None)
    if _contains_action_recommendation(_normalize(text or "")):
        work.violation("speculative_answer_with_clarification", "Clarification mode must not include a speculative recommendation.", refs=["answer_mode"], detected=text[:180] if text else None)
    work.check("clarification", RenderedCheckStatus.PASSED.value, True, "Clarification prompt checked.", ["direct_answer"])


def _validate_internal_terms(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    forbidden = ["answerpacket", "decisionoutput", "rulesevaluation", "calculationpacket", "recommendationvalidation", "stage 1", "stage 11", "stage 12", "serializer", "engine registry", "source ref", "source_ref"]
    hit = [term for term in forbidden if term in norm]
    if hit:
        work.violation("internal_terminology", "Rendered response exposes internal implementation terminology.", refs=["rendered_text"], detected=", ".join(hit))
    work.check("internal_terms", RenderedCheckStatus.PASSED.value, True, "Internal terminology checked.", ["rendered_text"])


def _validate_execution_claims(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    if _has_execution_claim(norm):
        work.violation("execution_claim", "Rendered response claims an action was executed.", refs=["forbidden_claims"], detected=text[:180] if text else None)
    work.check("execution_claims", RenderedCheckStatus.PASSED.value, True, "Execution claims checked.", ["forbidden_claims"])


def _validate_confidence_language(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    norm = _normalize(text or "")
    if packet.confidence in {"medium", "low", "unavailable"} and any(word in norm for word in {"guaranteed", "definitely", "unquestionably", "certain", "no doubt"}):
        work.violation("confidence_overstated", "Rendered response overstates confidence beyond the answer packet.", refs=["confidence"], detected=text[:180] if text else None)
    work.check("confidence_language", RenderedCheckStatus.PASSED.value, True, "Confidence language checked.", ["confidence"])


def _validate_formatting(packet: AnswerPacket, text: str | None, work: _RenderedWork) -> None:
    raw = text or ""
    norm = _normalize(raw)
    if "<script" in norm or "</script" in norm:
        work.violation("script_tag", "Rendered response includes executable script content.", refs=["rendered_text"], detected=raw[:180])
    if re.search(r"/Users/[^\s]+|/mnt/data/[^\s]+|[A-Z_]{8,}=", raw):
        work.violation("internal_path_or_secret", "Rendered response includes an internal path or secret-like content.", refs=["rendered_text"], detected=raw[:180])
    if raw.strip().startswith(("{", "[")) and ("answer_mode" in raw or "validation_status" in raw):
        work.violation("raw_packet_dump", "Rendered response appears to dump raw internal packet data.", refs=["rendered_text"], detected=raw[:180])
    work.check("formatting", RenderedCheckStatus.PASSED.value, True, "Markdown and unsafe formatting checked.", ["rendered_text"])


RENDERED_ANSWER_CHECKS: list[RenderedCheck] = [
    _validate_response_presence,
    _validate_response_mode,
    _validate_direct_answer,
    _validate_recommendation,
    _validate_legality,
    _validate_actionability,
    _validate_conditions,
    _validate_numbers,
    _validate_estimate_labels,
    _validate_forbidden_claims,
    _validate_entity_scope,
    _validate_limitations,
    _validate_clarification,
    _validate_internal_terms,
    _validate_execution_claims,
    _validate_confidence_language,
    _validate_formatting,
]


ACTION_SYNONYMS = {
    "accept": {"accept", "take", "approve"},
    "reject": {"reject", "decline", "pass"},
    "counter": {"counter", "counteroffer"},
    "acquire": {"acquire", "add", "pick up", "claim", "pursue"},
    "monitor": {"monitor", "watch"},
    "sell": {"sell", "shop", "trade away"},
    "retain": {"retain", "keep", "hold"},
    "draft_player": {"draft", "select"},
    "start": {"start"},
    "bench": {"bench", "sit"},
    "release": {"release", "cut"},
    "extend": {"extend", "extension"},
}

OPPOSITE_ACTIONS = {
    "accept": "reject",
    "reject": "accept",
    "acquire": "monitor",
    "monitor": "acquire",
    "start": "bench",
    "bench": "start",
    "extend": "release",
    "release": "extend",
}

GENERIC_ENTITY_TERMS = {
    "coach", "condor", "league", "team", "player", "draft", "pick", "trade", "contract",
    "salary", "cap", "roster", "recommendation", "verified", "conditions", "limitations",
}


def _validation_result(
    packet: AnswerPacket,
    original_text: str | None,
    approved_text: str,
    status: str,
    used_openai: bool,
    used_fallback: bool,
    work: _RenderedWork,
) -> RenderedAnswerValidation:
    return RenderedAnswerValidation(
        validation_status=status,
        original_rendered_text=original_text,
        approved_text=_bounded_text(approved_text),
        used_openai_response=used_openai,
        used_deterministic_fallback=used_fallback,
        checks=work.checks[:MAX_CHECKS],
        violations=work.violations[:MAX_VIOLATIONS],
        warnings=work.warnings[:MAX_WARNINGS],
        preserved_claim_refs=_dedupe(work.preserved),
        missing_claim_refs=_dedupe(work.missing),
        unsupported_claims=_dedupe(work.unsupported),
        answer_packet_version=packet.answer_version,
    )


def _display_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.$%/?_ -]+", " ", str(value or "").lower())).strip()


def _keywords(text: str) -> list[str]:
    stop = {
        "this", "that", "from", "with", "current", "verified", "recommendation",
        "deterministic", "requested", "season", "rules", "result", "available",
    }
    words = [
        word
        for word in _normalize(text).split()
        if len(word) >= 4 and word not in stop and not any(ch.isdigit() for ch in word)
    ]
    return _dedupe(words[:6])


def _concept_present(concept: str, normalized_text: str) -> bool:
    words = _keywords(concept)
    if not words:
        return True
    hits = sum(1 for word in words if word in normalized_text)
    return hits >= max(1, min(2, len(words)))


def _action_present(action: str, normalized_text: str) -> bool:
    return any(term in normalized_text for term in ACTION_SYNONYMS.get(action, {action.replace("_", " ")}))


def _opposite_action(action: str) -> str | None:
    return OPPOSITE_ACTIONS.get(action)


def _contains_action_recommendation(normalized_text: str) -> bool:
    recommendation_markers = {"i recommend", "my recommendation", "you should", "i would", "go ahead", "make the", "submit", "do it", "accept", "reject", "acquire", "release", "extend", "draft", "start"}
    return any(marker in normalized_text for marker in recommendation_markers)


def _opposes_claim(approved: str, rendered: str) -> bool:
    approved_norm = _normalize(approved)
    rendered_norm = _normalize(rendered)
    if "illegal" in approved_norm and _has_legal_claim(rendered_norm):
        return True
    if "cannot" in approved_norm and any(term in rendered_norm for term in {"can ", "should ", "ready"}):
        return True
    return False


def _has_legal_claim(normalized_text: str) -> bool:
    return bool(re.search(r"(?<!il)\blegal\b|\ballowed\b|\bpermitted\b|\bcleared\b", normalized_text))


def _has_confirmed_legal_claim(normalized_text: str) -> bool:
    return any(term in normalized_text for term in {"definitely legal", "confirmed legal", "fully legal", "cleared to", "allowed now", "permitted now"})


def _has_actionability_claim(normalized_text: str) -> bool:
    negated = {"not ready to act", "not ready to execute", "not cleared to proceed", "not ready yet"}
    if any(term in normalized_text for term in negated):
        stripped = normalized_text
        for term in negated:
            stripped = stripped.replace(term, "")
        normalized_text = stripped
    phrases = {"do it now", "submit it", "make the trade", "execute the move", "ready to execute", "ready to act", "cleared to proceed", "complete immediately", "go ahead"}
    return any(phrase in normalized_text for phrase in phrases)


def _has_execution_claim(normalized_text: str) -> bool:
    phrases = {
        "trade completed", "completed the trade", "i traded", "player released", "released the player",
        "submitted a waiver", "extended the contract", "drafted", "changed your lineup",
        "lineup changed", "contacted the owner", "settings changed", "roster modified",
    }
    return any(phrase in normalized_text for phrase in phrases)


def _has_estimate_label(normalized_text: str) -> bool:
    return any(term in normalized_text for term in {"approximately", "estimated", "projected", "roughly", "range", "about", "around"})


def _forbidden_detected(claim_type: str, normalized_text: str) -> bool:
    if claim_type in {"actionability", "confirmed_legality"}:
        return _has_actionability_claim(normalized_text) or _has_confirmed_legal_claim(normalized_text)
    if claim_type in {"exact_estimate"}:
        return "exact" in normalized_text and not _has_estimate_label(normalized_text)
    if claim_type in {"availability_claim"}:
        return any(term in normalized_text for term in {"available to add", "can be acquired", "is available"})
    if claim_type in {"acceptance_probability"}:
        return any(term in normalized_text for term in {"other owner will accept", "other owner would accept", "acceptance probability", "likely accept", "they will accept", "they would accept"})
    if claim_type in {"replacement_recommendation", "rejected_as_supported"}:
        return _contains_action_recommendation(normalized_text)
    if claim_type == "executed_action":
        return _has_execution_claim(normalized_text)
    if claim_type == "future_exact_pick_slot":
        return "exact slot" in normalized_text or "locked in at" in normalized_text
    return False


def _approved_numbers(packet: AnswerPacket) -> list[float]:
    numbers: list[float] = []
    if packet.direct_answer:
        numbers.extend(_numbers(packet.direct_answer.text))
    for fact in packet.facts:
        numbers.extend(_numbers(_format_value(fact.value)))
    for calc in packet.calculations:
        numbers.extend(_numbers(_format_value(calc.value)))
    for rule in packet.rule_conclusions:
        numbers.extend(_numbers(rule.explanation))
    for condition in packet.conditions:
        numbers.extend(_numbers(condition.explanation))
    return numbers


def _numbers(text: str) -> list[float]:
    out = []
    for match in re.finditer(r"\$?\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:m|million|%)?\b", text.lower()):
        raw = match.group(0).replace("$", "").replace(",", "").strip()
        multiplier = 1.0
        if raw.endswith("million"):
            raw = raw[: -len("million")].strip()
            multiplier = 1_000_000.0
        elif raw.endswith("m"):
            raw = raw[:-1].strip()
            multiplier = 1_000_000.0
        elif raw.endswith("%"):
            raw = raw[:-1].strip()
        try:
            out.append(float(raw) * multiplier)
        except ValueError:
            pass
    return out


def _numbers_equivalent(a: float, b: float) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) / scale <= 0.02


def _supported_entity_terms(packet: AnswerPacket) -> set[str]:
    terms = set()
    for fact in packet.facts:
        terms.add(_normalize(fact.label))
        terms.update(_normalize(entity) for entity in fact.entity_ids)
    if packet.recommendation:
        terms.add(_normalize(packet.recommendation.label))
        terms.update(_normalize(entity) for entity in packet.recommendation.related_entity_ids)
    for alt in packet.alternatives:
        terms.add(_normalize(alt.label))
    return {term for term in terms if term}


def _capitalized_terms(text: str) -> list[str]:
    terms = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}\b", text or "")
    terms.extend(re.findall(r"\bPlayer\s+[A-Z]\b|\bTeam\s+[A-Z]\b|\bPick\s+\d+\b", text or ""))
    cleaned = []
    for term in terms:
        words = term.split()
        if words and words[0].lower() in {"accept", "reject", "acquire", "monitor", "start", "bench", "release", "extend"}:
            words = words[1:]
        candidate = " ".join(words)
        if candidate and candidate.lower() not in {"my recommendation", "the recommendation", "this recommendation", "available cap"}:
            cleaned.append(candidate)
    return cleaned


def _snippet(text: str | None, normalized: str) -> str | None:
    return text[:180] if text else normalized[:180]


def _format_value(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, inner in value.items():
            if inner not in (None, "", [], {}):
                parts.append(f"{key}: {inner}")
        return ", ".join(parts)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value[:5])
    return str(value)


def _label_action(action: str) -> str:
    return action.replace("_", " ").title()


def _bounded_text(text: str) -> str:
    clean = str(text or "").strip()
    if len(clean) <= MAX_TEXT_CHARS:
        return clean
    return clean[: MAX_TEXT_CHARS - 20].rstrip() + "\n\n[Answer shortened.]"


def _dedupe(items: list[Any]) -> list[str]:
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
            if inner not in (None, "", [], {}) and key not in {"raw", "raw_row", "exception", "traceback", "sql", "service_key", "access_token", "refresh_token", "scratchpad", "chain_of_thought"}
        }
    if isinstance(value, list):
        return [_compact(item) for item in value if item not in (None, "", [], {})]
    return value
