"""Pick how hard League AI thinks about a question, and tag its topic for the weekly report.

No model call: a few keyword rules. Lookups ("what's my cap space?") get low effort,
decisions (trades, lineups, keep/cut) get high, everything else medium.
"""
from __future__ import annotations

import re

TOPICS = ("trade", "lineup", "waivers", "contracts", "draft", "strategy", "lookup", "other")

_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("trade", ("trade", "deal for", "offer", "package", "buy low", "sell high", "deadline")),
    ("lineup", ("start", "sit ", "bench", "lineup", "flex", "who do i play", "streamer", "stream ")),
    ("waivers", ("waiver", "pick up", "pickup", "free agent", "faab", "add ", "drop ")),
    ("contracts", ("contract", "cap ", "salary", "extension", "cut ", "release", "dead cap", "tag")),
    ("draft", ("draft", "rookie", "prospect", "pick ")),
    ("strategy", ("contender", "rebuild", "retool", "window", "plan", "long term", "dynasty value", "worth")),
]
_LOOKUP = re.compile(r"^(what('?s| is| are)|how (much|many)|who('?s| is| are) (on|my)|when|list|show|which team)\b")
_DECISION = re.compile(r"\b(should i|would you|better|or|vs\.?|versus|accept|decline|keep|worth it)\b")


def topic(question: str) -> str:
    q = f" {(question or '').lower()} "
    for name, words in _RULES:
        if any(w in q for w in words):
            return name
    return "lookup" if _LOOKUP.search(q.strip()) else "other"


def effort_for(question: str) -> str:
    q = (question or "").lower().strip()
    t = topic(q)
    if t in ("trade", "lineup", "strategy") or len(q) > 240 or (_DECISION.search(q) and t != "lookup"):
        return "high"
    if t == "lookup" or (_LOOKUP.search(q) and len(q) < 90):
        return "low"
    return "medium"
