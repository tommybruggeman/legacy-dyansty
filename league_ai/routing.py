"""Pick how hard League AI thinks about a question, and tag its topic for the weekly report.

No model call: a few keyword rules. Lookups ("what's my cap space?") get low effort,
decisions (trades, lineups, keep/cut) get high, everything else medium.
"""
from __future__ import annotations

import re

TOPICS = ("trade", "lineup", "waivers", "contracts", "draft", "strategy", "lookup", "other")

_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("lineup", ("start", "sit ", "bench", "lineup", "flex", "who do i play", "streamer", "stream ")),
    ("waivers", ("waiver", "pick up", "pickup", "free agent", "faab", "add ", "drop ")),
    ("contracts", ("contract", "cap ", "salary", "extension", "cut ", "release", "dead cap", "tag")),
    ("draft", ("draft", "rookie", "prospect", "pick ")),
    ("strategy", ("contender", "rebuild", "retool", "window", "plan", "long term", "dynasty value", "worth")),
]
_LOOKUP = re.compile(r"^(what('?s| is| are)|how (much|many)|who('?s| is| are) (on|my)|when|list|show|which team)\b")
_SWAP = re.compile(r"^[a-z.'\- ]{3,40} for [a-z.'\- ]{3,40}\??$")  # "Waddle for Devonta Smith?"
_DECISION = re.compile(r"\b(should i|would you|better|or|vs\.?|versus|accept|decline|keep|worth it)\b")


# Asking for trade advice, not just mentioning one: "how does Jefferson look now that Cousins was traded"
# is an outlook question, so it gets no trade talk and no "Open in Sleeper" button. The app mirrors this
# in mobile/src/lib/tradeQuestion.ts; keep the two in step.
_TRADE_ASK = re.compile(
    r"\btrade (for|away|him|her|them|my|me|[a-z.'\-]+ (for|to|and))\b"
    r"|\b(should|would|could|can|do) (i|we|you) (trade|sell|buy|move|shop|flip)\b"
    r"|\btrade (offer|value|target|idea|partner|package|ask|market|chip|proposal)s?\b"
    r"|\b(sell|buy)[ -](high|low|now|him|her|them)\b"
    r"|\b(find|make|propose|build|suggest|counter|accept|decline|any) (me )?(a |an |this |that |the |good )?trade\b"
    r"|\btrades? (that|to|with|i|we)\b"
    r"|\b(offers?|deal for|package|in return|straight up|straight swap|swap|worth trading|trading (for|away|him|her))\b"
    r"|\bin a trade\b|\bwhat (can|could|would) (i|we) get for\b"
    r"|\btrade\b.*\b(fair|accept|decline|worth it|win|lose|lopsided)\b"
    r"|\btrade\??$"
)


def is_trade_question(question: str) -> bool:
    """True when the owner wants trade advice (a deal, an offer, buy or sell), not when a trade is only the news."""
    q = re.sub(r"\s+", " ", (question or "").lower()).strip()
    return bool(_TRADE_ASK.search(q) or _SWAP.match(q))


def topic(question: str) -> str:
    q = f" {(question or '').lower()} "
    if is_trade_question(question):
        return "trade"
    for name, words in _RULES:
        if any(w in q for w in words):
            return name
    if _SWAP.match(q.strip()):
        return "trade"
    return "lookup" if _LOOKUP.search(q.strip()) else "other"


def effort_for(question: str) -> str:
    q = (question or "").lower().strip()
    t = topic(q)
    if t in ("trade", "lineup", "strategy") or len(q) > 240 or (_DECISION.search(q) and t != "lookup"):
        return "high"
    if t == "lookup" or (_LOOKUP.search(q) and len(q) < 90):
        return "low"
    return "medium"
