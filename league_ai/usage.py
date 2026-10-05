"""Token and dollar accounting for every League AI model call (chat, brief, grading, playbook).

Costs are computed from the token counts the API returns, at Anthropic's public list
prices (per million tokens). Batch requests are billed at half price. Rows land in
league_ai_usage and feed the weekly report.
"""
from __future__ import annotations

from typing import Any, Mapping

# $ per million tokens: input, output, 5-minute cache write, cache read
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
    "claude-haiku-4-5-20251001": (1.00, 5.00, 1.25, 0.10),
}
WEB_SEARCH_PER_REQUEST = 0.01  # $10 per 1,000 searches
BATCH_DISCOUNT = 0.5


def cost_usd(model: str, *, input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0, cache_write_tokens: int = 0,
             web_searches: int = 0, batch: bool = False) -> float | None:
    """List-price cost of one call; None for a model missing from the price table."""
    price = PRICES.get(model)
    if price is None:
        return None
    p_in, p_out, p_write, p_read = price
    tokens = (input_tokens * p_in + output_tokens * p_out + cache_write_tokens * p_write + cache_read_tokens * p_read) / 1_000_000
    if batch:
        tokens *= BATCH_DISCOUNT
    return round(tokens + web_searches * WEB_SEARCH_PER_REQUEST, 6)


def usage_row(*, league_id: str | None, user_id: str | None, feature: str, model: str, effort: str | None = None, topic: str | None = None,
              input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0, cache_write_tokens: int = 0, web_searches: int = 0,
              tool_calls: list[str] | None = None, rounds: int = 0, latency_ms: int = 0, batch: bool = False, ok: bool = True,
              conversation_id: str | None = None, prediction_id: str | None = None) -> dict[str, Any]:
    return {
        "league_id": league_id, "user_id": user_id, "feature": feature, "model": model, "effort": effort, "topic": topic,
        "input_tokens": int(input_tokens), "output_tokens": int(output_tokens), "cache_read_tokens": int(cache_read_tokens),
        "cache_write_tokens": int(cache_write_tokens), "web_searches": int(web_searches), "tool_calls": list(tool_calls or []),
        "rounds": int(rounds), "latency_ms": int(latency_ms), "batch": bool(batch), "ok": bool(ok),
        "conversation_id": conversation_id, "prediction_id": prediction_id,
        "cost_usd": cost_usd(model, input_tokens=input_tokens, output_tokens=output_tokens, cache_read_tokens=cache_read_tokens,
                             cache_write_tokens=cache_write_tokens, web_searches=web_searches, batch=batch),
    }


def row_from_trace(trace: Any, **kw: Any) -> dict[str, Any]:
    t = trace.as_dict() if hasattr(trace, "as_dict") else dict(trace)
    return usage_row(model=t.get("model") or "", effort=t.get("effort"), input_tokens=t.get("input_tokens") or 0, output_tokens=t.get("output_tokens") or 0,
                     cache_read_tokens=t.get("cache_read_tokens") or 0, cache_write_tokens=t.get("cache_creation_tokens") or 0,
                     tool_calls=t.get("tool_calls"), rounds=t.get("rounds") or 0, latency_ms=t.get("latency_ms") or 0,
                     ok=not t.get("error_code"), **kw)


def response_usage(resp: Any) -> dict[str, int]:
    """Token counts (and web searches) from an SDK response or batch result message."""
    u = getattr(resp, "usage", None)
    def g(obj: Any, name: str) -> int:
        try:
            return int((obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, 0)) or 0)
        except Exception:
            return 0
    stu = (u.get("server_tool_use") if isinstance(u, Mapping) else getattr(u, "server_tool_use", None)) if u is not None else None
    return {"input_tokens": g(u, "input_tokens"), "output_tokens": g(u, "output_tokens"), "cache_read_tokens": g(u, "cache_read_input_tokens"),
            "cache_write_tokens": g(u, "cache_creation_input_tokens"), "web_searches": g(stu, "web_search_requests") if stu is not None else 0}


def record(client: Any, row: Mapping[str, Any]) -> None:
    """Best effort: accounting must never break an answer."""
    try:
        client.table("league_ai_usage").insert(dict(row)).execute()
    except Exception as exc:
        print(f"LEAGUE_AI_USAGE_WRITE_FAILED {type(exc).__name__}", flush=True)
