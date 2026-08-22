from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from gm_assistant.openai_reasoning.models import (
    ProviderResult,
    REASONING_RESPONSE_SCHEMA,
    ReasoningConfigurationStatus,
    ReasoningAlternative,
    ReasoningRankedPlayer,
    ReasoningRequest,
    ReasoningResponse,
    ReasoningTrace,
)
from gm_assistant.openai_reasoning.prompt_builder import SYSTEM_INSTRUCTION, build_reasoning_messages

try:
    from openai import APITimeoutError, OpenAI, RateLimitError
except Exception:  # pragma: no cover
    APITimeoutError = TimeoutError
    RateLimitError = RuntimeError
    OpenAI = None


DEFAULT_REASONING_MODEL = "gpt-4.1-mini"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_OUTPUT_TOKENS = 1600


@dataclass(frozen=True)
class ReasoningConfig:
    enabled: bool
    model: str
    timeout_seconds: float
    max_output_tokens: int
    api_key_present: bool


def load_reasoning_config() -> ReasoningConfig:
    enabled_text = os.getenv("OPENAI_REASONING_ENABLED", "1").strip().lower()
    enabled = enabled_text not in {"0", "false", "no", "off"}
    model = (
        os.getenv("OPENAI_MODEL", "").strip()
        or os.getenv("OPENAI_GM_MODEL", "").strip()
        or DEFAULT_REASONING_MODEL
    )
    timeout = _safe_float(os.getenv("OPENAI_TIMEOUT_SECONDS")) or _safe_float(os.getenv("OPENAI_GM_TIMEOUT_SECONDS")) or DEFAULT_TIMEOUT_SECONDS
    max_output = _safe_int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS")) or DEFAULT_MAX_OUTPUT_TOKENS
    return ReasoningConfig(
        enabled=enabled,
        model=model,
        timeout_seconds=timeout,
        max_output_tokens=max(200, min(max_output, 3000)),
        api_key_present=bool(os.getenv("OPENAI_API_KEY", "").strip()),
    )


def configuration_status(*, live_test_flag: str | None = None) -> ReasoningConfigurationStatus:
    config = load_reasoning_config()
    provider = OpenAIReasoningProvider.from_environment()
    provider_name = type(provider).__name__.replace("ReasoningProvider", "") or "Unknown"
    live_flag = live_test_flag if live_test_flag is not None else os.getenv("LEGACY_OPENAI_LIVE_TEST", "")
    error = None
    if not config.enabled:
        error = "reasoning_disabled"
    elif not config.api_key_present:
        error = "missing_api_key"
    elif OpenAI is None:
        error = "openai_sdk_unavailable"
    return ReasoningConfigurationStatus(
        reasoning_enabled=config.enabled,
        api_key_present=config.api_key_present,
        provider_selected=provider_name,
        model_configured=bool(config.model),
        timeout_configured=config.timeout_seconds > 0,
        max_output_tokens_configured=config.max_output_tokens > 0,
        live_testing_permitted=str(live_flag).strip().lower() in {"1", "true", "yes", "on"},
        configuration_valid=error is None,
        safe_error_code=error,
    )


def live_smoke_permitted() -> bool:
    status = configuration_status()
    return status.live_testing_permitted and status.configuration_valid


class UnavailableReasoningProvider:
    def __init__(self, reason: str = "reasoning_unavailable") -> None:
        self.reason_code = reason
        self.calls: list[ReasoningRequest] = []

    def reason(self, request: ReasoningRequest) -> ProviderResult:
        self.calls.append(request)
        return ProviderResult(
            ok=False,
            error_code=self.reason_code,
            trace=ReasoningTrace(
                request_id=request.request_id,
                provider_status="unavailable",
                eligibility_decision="provider_unavailable",
                provider_selected="Unavailable",
                provider_called=False,
                provider_skipped_reason=self.reason_code,
                result_status="unavailable",
                safe_error_code=self.reason_code,
                fallback_status="deterministic_fallback",
            ),
        )


class FakeReasoningProvider:
    def __init__(self, response: ReasoningResponse | Exception | None = None, *, error_code: str | None = None) -> None:
        self.response = response or ReasoningResponse(
            answer_type="factual_explanation",
            direct_answer="Fake reasoning answer.",
            recommendation_strength="none",
            facts_used=["answer.direct_answer"],
        )
        self.error_code = error_code
        self.calls: list[ReasoningRequest] = []

    def reason(self, request: ReasoningRequest) -> ProviderResult:
        self.calls.append(request)
        if isinstance(self.response, Exception):
            return ProviderResult(ok=False, error_code=self.error_code or "provider_exception", trace=_trace(request, "failed", self.error_code or "provider_exception", provider_selected="Fake"))
        return ProviderResult(ok=True, response=self.response, trace=_trace(request, "success", None, response_type=self.response.answer_type, provider_selected="Fake"))


class OpenAIReasoningProvider:
    def __init__(self, *, client: Any | None = None, config: ReasoningConfig | None = None) -> None:
        self.config = config or load_reasoning_config()
        self.client = client

    @classmethod
    def from_environment(cls) -> "OpenAIReasoningProvider | UnavailableReasoningProvider":
        config = load_reasoning_config()
        if not config.enabled:
            return UnavailableReasoningProvider("reasoning_disabled")
        if not config.api_key_present:
            return UnavailableReasoningProvider("missing_api_key")
        if OpenAI is None:
            return UnavailableReasoningProvider("openai_sdk_unavailable")
        return cls(config=config)

    def reason(self, request: ReasoningRequest) -> ProviderResult:
        if not self.config.enabled:
            return ProviderResult(ok=False, error_code="reasoning_disabled", trace=_trace(request, "skipped", "reasoning_disabled"))
        if not self.config.api_key_present and self.client is None:
            return ProviderResult(ok=False, error_code="missing_api_key", trace=_trace(request, "skipped", "missing_api_key"))
        if OpenAI is None and self.client is None:
            return ProviderResult(ok=False, error_code="openai_sdk_unavailable", trace=_trace(request, "skipped", "openai_sdk_unavailable"))
        start = time.perf_counter()
        try:
            client = self.client or OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip(), timeout=self.config.timeout_seconds)
            messages = build_reasoning_messages(request)
            response = client.responses.create(
                model=self.config.model,
                instructions=SYSTEM_INSTRUCTION,
                input=messages,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "legacy_reasoning_response",
                        "schema": REASONING_RESPONSE_SCHEMA,
                        "strict": True,
                    }
                },
                max_output_tokens=self.config.max_output_tokens,
                tools=[],
            )
            response_text = _response_text(response)
            parsed = parse_reasoning_response(response_text)
            trace = _trace(request, "success", None, response_type=parsed.answer_type, latency_ms=int((time.perf_counter() - start) * 1000), model=self.config.model, section_count=len(messages), usage=_usage(response))
            return ProviderResult(ok=True, response=parsed, trace=trace)
        except APITimeoutError:
            return ProviderResult(ok=False, error_code="timeout", trace=_trace(request, "failed", "timeout", latency_ms=int((time.perf_counter() - start) * 1000), model=self.config.model))
        except RateLimitError as exc:
            return ProviderResult(
                ok=False,
                error_code="rate_limited",
                trace=_trace(
                    request,
                    "failed",
                    "rate_limited",
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    model=self.config.model,
                    error_details=_rate_limit_error_details(exc),
                ),
            )
        except ValueError as exc:
            return ProviderResult(
                ok=False,
                error_code="malformed_response",
                trace=_trace(
                    request,
                    "failed",
                    "malformed_response",
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    model=self.config.model,
                    error_details=_malformed_response_details(
                        response=locals().get("response"),
                        response_text=locals().get("response_text"),
                        exc=exc,
                    ),
                ),
            )
        except Exception:
            return ProviderResult(ok=False, error_code="provider_error", trace=_trace(request, "failed", "provider_error", latency_ms=int((time.perf_counter() - start) * 1000), model=self.config.model))


def parse_reasoning_response(value: str | dict[str, Any]) -> ReasoningResponse:
    payload = json.loads(value) if isinstance(value, str) else dict(value)
    if not isinstance(payload, dict) or not payload.get("direct_answer"):
        raise ValueError("empty_reasoning_response")
    _validate_reasoning_payload_shape(payload)
    alternatives = [
        ReasoningAlternative(
            label=str(item.get("label") or ""),
            description=str(item.get("description") or ""),
            strategic_purpose=str(item.get("strategic_purpose") or ""),
            required_verified_assets=[str(value) for value in item.get("required_verified_assets") or []],
            unverified_assumptions=[str(value) for value in item.get("unverified_assumptions") or []],
            requires_deterministic_simulation=bool(item.get("requires_deterministic_simulation")),
        )
        for item in payload.get("alternatives") or []
        if isinstance(item, dict)
    ]
    ranked_players = [
        ReasoningRankedPlayer(
            rank=int(item.get("rank") or 0),
            player_name=str(item.get("player_name") or ""),
            player_id=str(item.get("player_id") or ""),
            short_reason=str(item.get("short_reason") or ""),
            fact_refs=[str(value) for value in item.get("fact_refs") or []],
        )
        for item in payload.get("ranked_players") or []
        if isinstance(item, dict)
    ]
    return ReasoningResponse(
        answer_type=str(payload.get("answer_type") or ""),
        direct_answer=str(payload.get("direct_answer") or ""),
        recommendation=payload.get("recommendation"),
        recommendation_strength=str(payload.get("recommendation_strength") or "none"),
        key_reasons=[str(item) for item in payload.get("key_reasons") or []],
        main_risks=[str(item) for item in payload.get("main_risks") or []],
        alternatives=alternatives,
        ranked_players=ranked_players,
        clarifying_question=payload.get("clarifying_question"),
        facts_used=[str(item) for item in payload.get("facts_used") or []],
        limitations=[str(item) for item in payload.get("limitations") or []],
        constraint_conflicts=[str(item) for item in payload.get("constraint_conflicts") or []],
        requires_deterministic_follow_up=bool(payload.get("requires_deterministic_follow_up")),
    )


def _validate_reasoning_payload_shape(payload: dict[str, Any]) -> None:
    required = list(REASONING_RESPONSE_SCHEMA.get("required", []))
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError("missing_required_fields:" + ",".join(missing))
    unexpected = [key for key in payload if key not in REASONING_RESPONSE_SCHEMA.get("properties", {})]
    if unexpected:
        raise ValueError("unexpected_fields:" + ",".join(unexpected))
    list_fields = ("key_reasons", "main_risks", "alternatives", "ranked_players", "facts_used", "limitations", "constraint_conflicts")
    invalid_types = [key for key in list_fields if not isinstance(payload.get(key), list)]
    if not isinstance(payload.get("answer_type"), str):
        invalid_types.append("answer_type")
    if not isinstance(payload.get("direct_answer"), str):
        invalid_types.append("direct_answer")
    if payload.get("recommendation") is not None and not isinstance(payload.get("recommendation"), str):
        invalid_types.append("recommendation")
    if not isinstance(payload.get("recommendation_strength"), str):
        invalid_types.append("recommendation_strength")
    if payload.get("clarifying_question") is not None and not isinstance(payload.get("clarifying_question"), str):
        invalid_types.append("clarifying_question")
    if not isinstance(payload.get("requires_deterministic_follow_up"), bool):
        invalid_types.append("requires_deterministic_follow_up")
    for item in payload.get("ranked_players") or []:
        if not isinstance(item, dict):
            invalid_types.append("ranked_players.item")
            break
        if not isinstance(item.get("rank"), int):
            invalid_types.append("ranked_players.rank")
        if not isinstance(item.get("player_name"), str):
            invalid_types.append("ranked_players.player_name")
        if not isinstance(item.get("player_id"), str):
            invalid_types.append("ranked_players.player_id")
        if not isinstance(item.get("short_reason"), str):
            invalid_types.append("ranked_players.short_reason")
        if not isinstance(item.get("fact_refs"), list):
            invalid_types.append("ranked_players.fact_refs")
    if invalid_types:
        raise ValueError("invalid_field_types:" + ",".join(invalid_types))


def _response_text(response: Any) -> str:
    if isinstance(response, dict):
        if response.get("output_text"):
            return str(response["output_text"])
        output = response.get("output") or []
    else:
        text = getattr(response, "output_text", None)
        if text:
            return str(text)
        output = getattr(response, "output", []) or []
    for item in output:
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        for part in content or []:
            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            if text:
                return str(text)
    raise ValueError("empty_response_text")


def _trace(request: ReasoningRequest, status: str, error: str | None, *, response_type: str | None = None, latency_ms: int = 0, model: str | None = None, section_count: int = 0, usage: dict[str, int] | None = None, provider_selected: str = "OpenAI", error_details: dict[str, Any] | None = None) -> ReasoningTrace:
    usage = usage or {}
    return ReasoningTrace(
        request_id=request.request_id,
        provider_status=status,
        eligibility_decision="provider_called" if status == "success" else "provider_failed",
        provider_selected=provider_selected,
        provider_called=True,
        provider_skipped_reason=None if status == "success" else error,
        result_status=status,
        schema_parse_status="parsed" if status == "success" else None,
        model_label=model,
        prompt_section_count=section_count,
        evidence_domains_included=[key for key, value in request.to_payload().items() if key.endswith("intelligence") and value],
        response_type=response_type,
        latency_ms=latency_ms,
        safe_error_code=error,
        provider_error_details=error_details or {},
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


def _rate_limit_error_details(exc: BaseException) -> dict[str, Any]:
    response = getattr(exc, "response", None)
    headers = _headers_from_response(response)
    body = getattr(exc, "body", None)
    error_payload = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
    details = {
        "message": _safe_error_text(str(exc)),
        "http_status": _safe_int(getattr(exc, "status_code", None) or getattr(response, "status_code", None)),
        "openai_error_code": _safe_header_value(getattr(exc, "code", None) or error_payload.get("code")),
        "openai_error_type": _safe_header_value(getattr(exc, "type", None) or error_payload.get("type")),
        "request_id": _safe_header_value(getattr(exc, "request_id", None) or headers.get("x-request-id") or headers.get("openai-request-id")),
        "retry_after": _safe_header_value(headers.get("retry-after")),
        "rate_limit_headers": {
            key: value
            for key, value in sorted(headers.items())
            if key.startswith("x-ratelimit-") or key.startswith("openai-ratelimit-")
        },
    }
    return {key: value for key, value in details.items() if value not in (None, "", {})}


def _malformed_response_details(*, response: Any, response_text: Any, exc: BaseException) -> dict[str, Any]:
    text = str(response_text or "")
    parsed: Any = None
    json_error: str | None = None
    try:
        parsed = json.loads(text) if text else None
    except Exception as err:
        json_error = type(err).__name__
    parsed_is_object = isinstance(parsed, dict)
    payload = parsed if parsed_is_object else {}
    required = list(REASONING_RESPONSE_SCHEMA.get("required", []))
    properties = set(REASONING_RESPONSE_SCHEMA.get("properties", {}))
    missing = [key for key in required if key not in payload] if payload else required
    unexpected = [key for key in payload if key not in properties]
    invalid_types = _invalid_reasoning_field_types(payload) if payload else []
    details = {
        "parse_error": _safe_error_text(str(exc)),
        "schema_check": _schema_check_from_exception(exc),
        "raw_provider_output_shape": _provider_output_shape(response, text),
        "response_output_item_types": _response_output_item_types(response),
        "response_text_length": len(text),
        "response_text_first_500": _safe_error_text(text[:500]),
        "response_text_last_500": _safe_error_text(text[-500:]) if text else None,
        "parsed_output_type": type(parsed).__name__ if parsed is not None else None,
        "parsed_top_level_keys": sorted(payload.keys()) if payload else [],
        "parsed_output": _sanitized_parsed_output(payload) if payload else None,
        "missing_required_fields": missing,
        "invalid_field_types": invalid_types,
        "unexpected_fields": unexpected,
        "array_lengths": _array_lengths(payload),
        "json_parse_error": json_error,
        "json_parse_error_message": _json_parse_error_message(text),
        "json_parse_error_position": _json_parse_error_position(text),
        "unknown_ids": [],
        "mismatch_kind": _malformed_mismatch_kind(json_error, parsed_is_object, missing, invalid_types, unexpected, text),
    }
    return {key: value for key, value in details.items() if value not in (None, "", {}, [])}


def _invalid_reasoning_field_types(payload: dict[str, Any]) -> list[str]:
    invalid: list[str] = []
    for key in ("key_reasons", "main_risks", "alternatives", "ranked_players", "facts_used", "limitations", "constraint_conflicts"):
        if key in payload and not isinstance(payload.get(key), list):
            invalid.append(key)
    if "answer_type" in payload and not isinstance(payload.get("answer_type"), str):
        invalid.append("answer_type")
    if "direct_answer" in payload and not isinstance(payload.get("direct_answer"), str):
        invalid.append("direct_answer")
    if "recommendation" in payload and payload.get("recommendation") is not None and not isinstance(payload.get("recommendation"), str):
        invalid.append("recommendation")
    if "recommendation_strength" in payload and not isinstance(payload.get("recommendation_strength"), str):
        invalid.append("recommendation_strength")
    if "clarifying_question" in payload and payload.get("clarifying_question") is not None and not isinstance(payload.get("clarifying_question"), str):
        invalid.append("clarifying_question")
    if "requires_deterministic_follow_up" in payload and not isinstance(payload.get("requires_deterministic_follow_up"), bool):
        invalid.append("requires_deterministic_follow_up")
    return invalid


def _provider_output_shape(response: Any, text: str) -> dict[str, Any]:
    output = response.get("output") if isinstance(response, dict) else getattr(response, "output", None)
    status = response.get("status") if isinstance(response, dict) else getattr(response, "status", None)
    incomplete = response.get("incomplete_details") if isinstance(response, dict) else getattr(response, "incomplete_details", None)
    details = {
        "response_type": type(response).__name__ if response is not None else None,
        "output_text_present": bool(text),
        "output_text_length": len(text),
        "output_item_count": len(output) if isinstance(output, list) else None,
        "status": _safe_header_value(status),
        "incomplete_details_present": bool(incomplete),
        "likely_truncated": _likely_truncated(response, text),
    }
    return {key: value for key, value in details.items() if value not in (None, "", {})}


def _response_output_item_types(response: Any) -> list[str]:
    output = response.get("output") if isinstance(response, dict) else getattr(response, "output", None)
    if not isinstance(output, list):
        return []
    types: list[str] = []
    for item in output[:10]:
        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if item_type:
            types.append(str(item_type))
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if isinstance(content, list):
            for part in content[:5]:
                part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
                if part_type:
                    types.append(str(part_type))
    return types


def _sanitized_parsed_output(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in REASONING_RESPONSE_SCHEMA.get("properties", {}):
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, str):
            out[key] = _safe_error_text(value)[:800]
        elif isinstance(value, list):
            out[key] = [_safe_error_text(str(item))[:300] for item in value[:8]]
        else:
            out[key] = value
    return out


def _array_lengths(payload: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(value)
        for key, value in payload.items()
        if isinstance(value, list)
    }


def _schema_check_from_exception(exc: BaseException) -> str:
    text = str(exc)
    if text.startswith("missing_required_fields:"):
        return "missing_required_fields"
    if text.startswith("unexpected_fields:"):
        return "unexpected_fields"
    if text.startswith("invalid_field_types:"):
        return "invalid_field_types"
    if "empty_reasoning_response" in text:
        return "empty_or_missing_direct_answer"
    if isinstance(exc, json.JSONDecodeError):
        return "json_parse"
    return "schema_validation"


def _json_parse_error_message(text: str) -> str | None:
    if not text:
        return None
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return _safe_error_text(exc.msg)
    except Exception as exc:
        return _safe_error_text(str(exc))
    return None


def _json_parse_error_position(text: str) -> int | None:
    if not text:
        return None
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return exc.pos
    except Exception:
        return None
    return None


def _malformed_mismatch_kind(
    json_error: str | None,
    parsed_is_object: bool,
    missing: list[str],
    invalid_types: list[str],
    unexpected: list[str],
    text: str,
) -> str:
    if json_error:
        return "formatting_or_truncation" if text else "missing_output"
    if text and not parsed_is_object:
        return "non_object_output"
    if missing:
        return "missing_required_fields"
    if invalid_types:
        return "invalid_field_types"
    if unexpected:
        return "unexpected_fields"
    return "schema_validation_failure"


def _likely_truncated(response: Any, text: str) -> bool:
    incomplete = response.get("incomplete_details") if isinstance(response, dict) else getattr(response, "incomplete_details", None)
    if incomplete:
        return True
    stripped = text.rstrip()
    return bool(stripped and not stripped.endswith(("}", "]")))


def _headers_from_response(response: Any) -> dict[str, str]:
    raw = getattr(response, "headers", None)
    if not raw:
        return {}
    items = raw.items() if hasattr(raw, "items") else []
    return {
        str(key).strip().lower(): _safe_header_value(value)
        for key, value in items
        if _safe_header_value(value)
    }


def _safe_error_text(value: Any) -> str:
    text = str(value or "").replace("\n", " ").strip()
    redacted = []
    for part in text.split():
        if part.lower().startswith(("bearer ", "authorization:")) or part.startswith("sk-"):
            redacted.append("[redacted]")
        else:
            redacted.append(part)
    return " ".join(redacted)[:600]


def _safe_header_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\n", " ").strip()
    if not text or text.startswith("sk-") or "authorization" in text.lower():
        return None
    return text[:200]


def _usage(response: Any) -> dict[str, int]:
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    if not usage:
        return {}
    get = usage.get if isinstance(usage, dict) else lambda key, default=None: getattr(usage, key, default)
    out = {}
    for source, target in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"), ("total_tokens", "total_tokens")):
        value = get(source)
        try:
            if value is not None:
                out[target] = int(value)
        except Exception:
            pass
    return out


def _safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None
