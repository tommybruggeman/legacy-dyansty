"""Thin wrapper over the Anthropic SDK: one call that runs the tool loop.

Safe by construction: errors become short codes, secrets never appear in
traces, and a fake SDK client can be injected for tests.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from league_ai.config import LeagueAIConfig, api_key, load_config
from league_ai.tools import ToolRegistry

try:  # pragma: no cover - exercised only with the real SDK installed
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None


@dataclass
class Trace:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    tool_calls: list[str] = field(default_factory=list)
    rounds: int = 0
    latency_ms: int = 0
    stop_reason: str | None = None
    error_code: str | None = None
    content_types: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "tool_calls": list(self.tool_calls),
            "rounds": self.rounds,
            "latency_ms": self.latency_ms,
            "stop_reason": self.stop_reason,
            "error_code": self.error_code,
            "content_types": list(self.content_types),
        }


@dataclass
class ClientResult:
    ok: bool
    text: str
    trace: Trace
    error_code: str | None = None


def _redact(text: str) -> str:
    return " ".join("[redacted]" if p.startswith("sk-") else p for p in str(text).split())[:400]


def _block_attr(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _content_to_api(content: Any) -> list[dict[str, Any]]:
    """Serialize assistant content blocks so they can be sent back in history.

    Thinking blocks must be returned verbatim (signature included) when the
    turn continues with tool results, so anything that is not text/tool_use is
    passed through as the SDK serializes it.
    """
    out = []
    for block in content or []:
        kind = _block_attr(block, "type")
        if kind == "text":
            out.append({"type": "text", "text": _block_attr(block, "text", "")})
        elif kind == "tool_use":
            out.append({"type": "tool_use", "id": _block_attr(block, "id"), "name": _block_attr(block, "name"), "input": _block_attr(block, "input") or {}})
        elif isinstance(block, dict):
            out.append(dict(block))
        elif hasattr(block, "model_dump"):
            out.append(block.model_dump(exclude_none=True))
    return out


def _usage(trace: Trace, response: Any) -> None:
    usage = _block_attr(response, "usage")
    if not usage:
        return
    for src, dst in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cache_read_input_tokens", "cache_read_tokens"),
        ("cache_creation_input_tokens", "cache_creation_tokens"),
    ):
        try:
            setattr(trace, dst, getattr(trace, dst) + int(_block_attr(usage, src, 0) or 0))
        except Exception:
            pass


class ClaudeClient:
    def __init__(self, config: LeagueAIConfig | None = None, sdk_client: Any | None = None) -> None:
        self.config = config or load_config()
        self._sdk = sdk_client

    def _client(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        if anthropic is None:
            raise RuntimeError("sdk_unavailable")
        key = api_key()
        if not key:
            raise RuntimeError("missing_api_key")
        self._sdk = anthropic.Anthropic(api_key=key, timeout=self.config.timeout_seconds, max_retries=2)
        return self._sdk

    def run(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: ToolRegistry | None = None,
        max_tool_calls: int | None = None,
    ) -> ClientResult:
        trace = Trace(model=self.config.model)
        start = time.perf_counter()
        limit = self.config.max_tool_calls if max_tool_calls is None else max_tool_calls
        try:
            client = self._client()
        except RuntimeError as exc:
            trace.error_code = str(exc)
            return ClientResult(False, "", trace, str(exc))

        history = [dict(m) for m in messages]
        tool_specs = tools.specs() if tools and len(tools) else []
        text_out = ""
        try:
            while True:
                kwargs: dict[str, Any] = {
                    "model": self.config.model,
                    "max_tokens": self.config.max_output_tokens,
                    "system": system,
                    "messages": history,
                }
                if tool_specs:
                    kwargs["tools"] = tool_specs
                response = client.messages.create(**kwargs)
                trace.rounds += 1
                _usage(trace, response)
                content = _block_attr(response, "content") or []
                trace.stop_reason = _block_attr(response, "stop_reason")
                trace.content_types = [str(_block_attr(b, "type")) for b in content]
                text_out = "\n".join(_block_attr(b, "text", "") for b in content if _block_attr(b, "type") == "text").strip()
                tool_uses = [b for b in content if _block_attr(b, "type") == "tool_use"]
                if trace.stop_reason != "tool_use" or not tool_uses:
                    break
                history.append({"role": "assistant", "content": _content_to_api(content)})
                results = []
                for block in tool_uses:
                    name = _block_attr(block, "name")
                    if len(trace.tool_calls) >= limit:
                        result_text = "Tool call limit reached for this answer. Answer now with the information you already have, and say what you could not check."
                    else:
                        trace.tool_calls.append(name)
                        result_text = tools.call(name, _block_attr(block, "input") or {}) if tools else "{}"
                    results.append({"type": "tool_result", "tool_use_id": _block_attr(block, "id"), "content": result_text})
                history.append({"role": "user", "content": results})
                if trace.rounds > limit + 2:
                    break
        except Exception as exc:  # map SDK errors to safe codes
            trace.error_code = _classify(exc)
            trace.latency_ms = int((time.perf_counter() - start) * 1000)
            return ClientResult(False, "", trace, trace.error_code)
        trace.latency_ms = int((time.perf_counter() - start) * 1000)
        if not text_out:
            trace.error_code = "empty_response" if trace.stop_reason != "max_tokens" else "output_budget_exhausted"
            return ClientResult(False, "", trace, trace.error_code)
        if trace.stop_reason == "max_tokens":
            text_out += "\n\n_(answer cut off: output budget reached)_"
        return ClientResult(True, text_out, trace)


def _classify(exc: BaseException) -> str:
    name = type(exc).__name__
    if anthropic is not None:
        if isinstance(exc, getattr(anthropic, "AuthenticationError", ())):
            return "auth_error"
        if isinstance(exc, getattr(anthropic, "RateLimitError", ())):
            return "rate_limited"
        if isinstance(exc, getattr(anthropic, "APITimeoutError", ())):
            return "timeout"
        if isinstance(exc, getattr(anthropic, "APIConnectionError", ())):
            return "connection_error"
        if isinstance(exc, getattr(anthropic, "BadRequestError", ())):
            return "bad_request"
    lowered = name.lower()
    if "timeout" in lowered:
        return "timeout"
    if "ratelimit" in lowered:
        return "rate_limited"
    if "auth" in lowered:
        return "auth_error"
    return "provider_error"


HUMAN_MESSAGES = {
    "missing_api_key": "League AI isn't configured yet: no Anthropic API key is set.",
    "sdk_unavailable": "League AI isn't installed on this deployment (the anthropic package is missing).",
    "auth_error": "League AI could not authenticate with Anthropic. Check the API key.",
    "rate_limited": "League AI is rate-limited right now. Try again in a moment.",
    "timeout": "League AI took too long to answer. Try again or ask a narrower question.",
    "connection_error": "League AI couldn't reach Anthropic. Check the network and try again.",
    "bad_request": "League AI sent a request Anthropic rejected. This is a bug worth reporting.",
    "empty_response": "League AI returned nothing. Try rephrasing the question.",
    "output_budget_exhausted": "League AI ran out of output budget before answering. Raise LEAGUE_AI_MAX_OUTPUT_TOKENS or ask a narrower question.",
    "provider_error": "League AI hit an unexpected provider error. Try again.",
    "disabled": "League AI is turned off for this deployment.",
    "league_state_unavailable": "League AI could not load the league state. Open My Team first, then try again.",
}


def human_message(code: str | None) -> str:
    return HUMAN_MESSAGES.get(code or "", "League AI could not answer that. Try again.")
