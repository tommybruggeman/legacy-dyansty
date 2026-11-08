"""Check every GM answer against the data it was given before the owner sees it. One cheap model call.

The GM answers with Sonnet (or Opus) from the league pack and its tool results. Nothing used to check the written
answer: a misquoted projection, salary, record or rank went straight to the owner. Here a small, cheap model
(Haiku) reads the answer next to the exact data behind it and lists any figure or fact the data contradicts. When it
finds one, it returns the same answer with only those facts corrected; the owner gets the fixed version.

It fails open: any error, an unclear verdict, or a "fix" that rewrites more than the wrong facts keeps the original
answer. Judgment calls (start A over B, a trade opinion) are never changed, only facts.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

MODEL = "claude-haiku-5-5"
MAX_EVIDENCE_CHARS = 60000   # tool results passed to the checker (newest kept when over)
MAX_PACK_CHARS = 120000      # the league pack is long; the checker gets all of it up to this
MIN_KEEP_SHARE = 0.6         # a "corrected" answer shorter than this share of the original is a rewrite, not a fix

SYSTEM = """You fact-check answers written by a fantasy football co-GM for a dynasty salary-cap league.

You get the league data the GM had (LEAGUE PACK), what its lookups returned (LOOKUPS), and its ANSWER.
Find statements in the ANSWER that the data CONTRADICTS: a wrong number (points, projection, salary, cap space,
years left, record, rank, percent, pick year or round), a player on the wrong team or roster, a wrong injury status,
a bye week that is wrong, or a claim the data plainly shows is false.

Rules
- Only flag what the data contradicts. If the data doesn't cover a statement, leave it alone.
- Never flag opinions, recommendations, predictions or reasoning (who to start, whether a trade is good).
- Small rounding is fine (17.84 quoted as 17.8 or "about 18").
- Generic PPR figures (fantasy_points_ppr, ppg_ppr) and league-scoring figures (ppg_league, league_points,
  this_week_projection) differ on purpose; only flag a number that matches neither.
- If nothing is wrong, verdict "ok" and an empty list.
- If something is wrong, verdict "fix", list each problem, and give corrected_answer: the ANSWER with only the wrong
  facts changed (same wording, length, order and formatting otherwise). If a wrong fact was the reason for the
  recommendation, correct the fact and leave the recommendation; say nothing about the correction itself."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ok", "fix"]},
        "problems": {"type": "array", "items": {"type": "object", "properties": {
            "said": {"type": "string"}, "data_says": {"type": "string"}}, "required": ["said", "data_says"], "additionalProperties": False}},
        "corrected_answer": {"type": "string"},
    },
    "required": ["verdict", "problems", "corrected_answer"],
    "additionalProperties": False,
}


@dataclass
class Check:
    ran: bool
    fixed: bool = False
    text: str = ""
    problems: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None


def enabled() -> bool:
    return (os.getenv("LEAGUE_AI_FACT_CHECK") or "on").strip().lower() not in {"0", "off", "false", "no"}


def _lookups(evidence: list[Mapping[str, Any]]) -> str:
    parts, total = [], 0
    for e in reversed(evidence or []):  # newest first, so the final research survives the cut
        block = f"## {e.get('tool')} {json.dumps(e.get('input') or {}, sort_keys=True, default=str)}\n{e.get('result') or ''}"
        if total + len(block) > MAX_EVIDENCE_CHARS:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(reversed(parts)) or "(no lookups)"


def check(answer: str, *, pack: str, evidence: list[Mapping[str, Any]], week_context: str | None = None,
          sdk_client: Any = None) -> Check:
    """Run the checker. Returns Check(fixed=True, text=corrected) only for a clean, targeted correction."""
    if not answer or not answer.strip():
        return Check(ran=False)
    start = time.perf_counter()
    try:
        client = sdk_client
        if client is None:
            import anthropic

            from league_ai.config import api_key

            client = anthropic.Anthropic(api_key=api_key(), timeout=30.0, max_retries=1)
        pack_text = (pack or "")[:MAX_PACK_CHARS]
        data = [
            # the pack is the same for every answer this owner gets for a few minutes: cache it
            {"type": "text", "text": "LEAGUE PACK\n" + pack_text + ("\n\nTHIS WEEK\n" + week_context if week_context else ""),
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "LOOKUPS\n" + _lookups(list(evidence or []))},
            {"type": "text", "text": "ANSWER\n" + answer},
        ]
        resp = client.messages.create(
            model=MODEL, max_tokens=4000, system=SYSTEM,
            messages=[{"role": "user", "content": data}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        usage = _usage(resp)
        raw = "".join(getattr(b, "text", "") for b in (getattr(resp, "content", None) or []) if getattr(b, "type", "") == "text")
        out = json.loads(raw)
    except Exception as exc:
        return Check(ran=False, error=type(exc).__name__, latency_ms=int((time.perf_counter() - start) * 1000))
    latency = int((time.perf_counter() - start) * 1000)
    problems = [p for p in (out.get("problems") or []) if isinstance(p, Mapping)]
    fixed_text = str(out.get("corrected_answer") or "").strip()
    if out.get("verdict") != "fix" or not problems or not fixed_text:
        return Check(ran=True, text=answer, problems=[], usage=usage, latency_ms=latency)
    if len(fixed_text) < MIN_KEEP_SHARE * len(answer.strip()) or fixed_text == answer.strip():
        # a rewrite, or a "fix" that changed nothing: keep the original, still count what it found
        return Check(ran=True, text=answer, problems=problems, usage=usage, latency_ms=latency, error="fix_rejected")
    return Check(ran=True, fixed=True, text=fixed_text, problems=problems, usage=usage, latency_ms=latency)


def _usage(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    get = (lambda k: int(getattr(u, k, 0) or 0)) if u is not None else (lambda k: 0)
    return {"input_tokens": get("input_tokens"), "output_tokens": get("output_tokens"),
            "cache_read_tokens": get("cache_read_input_tokens"), "cache_write_tokens": get("cache_creation_input_tokens")}
