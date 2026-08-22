from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


VALID_DOMAINS = {
    "team", "player", "position", "trade", "contract",
    "rookie", "free_agent", "lineup", "strategy", "data_quality",
    "unknown",
}


@dataclass
class AIUnderstanding:
    domain: str = "unknown"
    intent: str = "GENERAL_GM_QUESTION"
    players: list[str] | None = None
    positions: list[str] | None = None
    action: str | None = None
    confidence: float = 0.0
    needs_roster: bool = True
    needs_player: bool = False
    raw_question: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["players"] = d["players"] or []
        d["positions"] = d["positions"] or []
        return d


def _fallback(question: str) -> AIUnderstanding:
    q = (question or "").lower()

    if any(x in q for x in ["rookie", "draft", "1.01", "1.02", "1.03"]):
        return AIUnderstanding(domain="rookie", intent="ROOKIE_DRAFT_PICK_DECISION", confidence=0.55, raw_question=question)

    if any(x in q for x in ["contract", "salary", "overpaid", "underpaid", "best value", "worth his salary"]):
        if any(x in q for x in ["best value", "underpaid", "best contract", "points per dollar"]):
            return AIUnderstanding(domain="contract", intent="CONTRACT_BEST_VALUE", confidence=0.65, raw_question=question)
        return AIUnderstanding(domain="contract", intent="CONTRACT_AUDIT", confidence=0.65, raw_question=question)

    if any(x in q for x in ["trade", "move", "sell", "cut", "drop", "get rid"]):
        return AIUnderstanding(domain="trade", intent="ROSTER_EXIT_DECISION", confidence=0.55, raw_question=question)

    if any(x in q for x in ["qb room", "rb room", "wr room", "te room", "rank my"]):
        return AIUnderstanding(domain="position", intent="POSITION_REVIEW", confidence=0.6, raw_question=question)

    if any(x in q for x in ["team look", "gm summary", "3 step", "next move", "offseason", "contention window"]):
        return AIUnderstanding(domain="strategy", intent="TEAM_REVIEW", confidence=0.6, raw_question=question)

    return AIUnderstanding(raw_question=question)


def understand_question_ai(question: str, known_players: list[str] | None = None) -> dict:
    if OpenAI is None or not os.getenv("OPENAI_API_KEY"):
        return _fallback(question).to_dict()

    client = OpenAI()

    known_players = known_players or []
    player_hint = ", ".join(known_players[:80])

    prompt = f"""
You classify fantasy dynasty GM questions.

Return ONLY valid JSON with:
domain: one of {sorted(VALID_DOMAINS)}
intent: uppercase snake case
players: list of player names mentioned
positions: list using QB,RB,WR,TE,PICK
action: short action string
confidence: 0 to 1
needs_roster: boolean
needs_player: boolean

Common intents:
TEAM_REVIEW
TEAM_STRENGTHS
TEAM_WEAKNESSES
TEAM_WINDOW
GM_PLAN
POSITION_REVIEW
POSITION_RANKING
ROSTER_EXIT_DECISION
PLAYER_TRADE_DECISION
PLAYER_HOLD_DECISION
TRADE_PACKAGE
TRADE_STRATEGY
QB_SURPLUS_TO_RB_STRATEGY
CONTRACT_AUDIT
CONTRACT_BEST_VALUE
PLAYER_CONTRACT_FIT
PRODUCTION_REVIEW
DATA_QUALITY_REVIEW
FREE_AGENT_TARGETS
ROOKIE_DRAFT_PICK_DECISION
ROOKIE_POSITION_VALUE
ROOKIE_PLAYER_DECISION
ROOKIE_PLAYER_COMPARISON
GENERAL_GM_QUESTION

Known player examples:
{player_hint}

Question:
{question}
"""

    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_UNDERSTANDING_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "You are a strict JSON classifier for a fantasy dynasty GM assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )

        text = resp.choices[0].message.content or "{}"
        data = json.loads(text)

        domain = str(data.get("domain") or "unknown")
        if domain not in VALID_DOMAINS:
            domain = "unknown"

        return AIUnderstanding(
            domain=domain,
            intent=str(data.get("intent") or "GENERAL_GM_QUESTION"),
            players=data.get("players") or [],
            positions=data.get("positions") or [],
            action=data.get("action"),
            confidence=float(data.get("confidence") or 0),
            needs_roster=bool(data.get("needs_roster", True)),
            needs_player=bool(data.get("needs_player", False)),
            raw_question=question,
        ).to_dict()

    except Exception:
        return _fallback(question).to_dict()
