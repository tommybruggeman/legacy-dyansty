from __future__ import annotations

import json
import os
from functools import lru_cache

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


CANONICAL_INTENTS = [
    "TEAM_REVIEW",
    "TEAM_STRENGTHS",
    "TEAM_WEAKNESSES",
    "TEAM_WINDOW",
    "GM_PLAN",
    "POSITION_REVIEW",
    "POSITION_RANKING",
    "ROSTER_EXIT_DECISION",
    "CUT_OR_CHURN",
    "PLAYER_TRADE_DECISION",
    "PLAYER_HOLD_DECISION",
    "TRADE_STRATEGY",
    "TRADE_PACKAGE",
    "QB_SURPLUS_TO_RB_STRATEGY",
    "CONTRACT_AUDIT",
    "CONTRACT_BEST_VALUE",
    "PLAYER_CONTRACT_FIT",
    "PRODUCTION_REVIEW",
    "DATA_QUALITY_REVIEW",
    "FREE_AGENT_TARGETS",
    "ROOKIE_DRAFT_PICK_DECISION",
    "ROOKIE_PLAYER_DECISION",
    "ROOKIE_PLAYER_COMPARISON",
    "ROOKIE_POSITION_VALUE",
    "LINEUP_DECISION",
    "TAXI_DECISION",
    "GENERAL_GM_QUESTION",
]


def _rules_fallback(question: str) -> dict:
    q = (question or "").lower()
    positions = [p for p in ["QB", "RB", "WR", "TE"] if p.lower() in q or f"{p.lower()}s" in q]

    if "fallback" in q or "unreliable" in q or "source work" in q or "low production confidence" in q:
        intent = "DATA_QUALITY_REVIEW"
    elif "best value" in q or "underpaid" in q or "best contract" in q or "points per dollar" in q:
        intent = "CONTRACT_BEST_VALUE"
    elif "contract" in q or "overpaid" in q or "salary" in q or "worst contract" in q:
        intent = "CONTRACT_AUDIT"
    elif "rookie" in q or "draft" in q:
        intent = "ROOKIE_DRAFT_PICK_DECISION"
    elif "trade package" in q or "package" in q:
        intent = "TRADE_PACKAGE"
    elif "what kind of trade" in q or "trade deadline" in q or "trade picks" in q or "trade for veterans" in q:
        intent = "TRADE_STRATEGY"
    elif "should i trade" in q or "should i move" in q or "should i sell" in q:
        intent = "PLAYER_TRADE_DECISION"
    elif "hold" in q or "not trade" in q or "protect" in q:
        intent = "PLAYER_HOLD_DECISION"
    elif "cut" in q or "drop" in q or "droppable" in q or "churn" in q or "clogger" in q:
        intent = "CUT_OR_CHURN"
    elif "room" in q or "rank my" in q or positions:
        intent = "POSITION_REVIEW"
    elif "flex" in q or "start" in q:
        intent = "LINEUP_DECISION"
    elif "taxi" in q:
        intent = "TAXI_DECISION"
    elif "free agent" in q or "fa " in q or "fas" in q or "waiver" in q:
        intent = "FREE_AGENT_TARGETS"
    elif "3 step" in q or "next move" in q or "offseason" in q or "gm summary" in q or "what should i do" in q or "contention window" in q:
        intent = "GM_PLAN"
    elif "team look" in q or "contender" in q or "rebuild" in q:
        intent = "TEAM_REVIEW"
    else:
        intent = "GENERAL_GM_QUESTION"

    return {
        "intent": intent,
        "domain": intent.split("_")[0].lower(),
        "players": [],
        "positions": positions,
        "confidence": 0.55,
        "source": "rules_fallback",
    }


@lru_cache(maxsize=512)
def classify_intent(question: str, known_players_csv: str = "") -> dict:
    if OpenAI is None or not os.getenv("OPENAI_API_KEY"):
        return _rules_fallback(question)

    client = OpenAI()

    prompt = {
        "task": "Classify this fantasy dynasty GM question. Return JSON only.",
        "question": question,
        "valid_intents": CANONICAL_INTENTS,
        "known_players": known_players_csv.split(",")[:120],
        "schema": {
            "intent": "one valid intent",
            "domain": "team/player/position/trade/contract/rookie/free_agent/lineup/data_quality/strategy/general",
            "players": ["exact player names mentioned"],
            "positions": ["QB/RB/WR/TE/PICK"],
            "confidence": "0 to 1",
        },
        "examples": [
            {"q": "should I trade Bryce Young?", "intent": "PLAYER_TRADE_DECISION"},
            {"q": "what kind of trade should I make?", "intent": "TRADE_STRATEGY"},
            {"q": "who has fallback production data?", "intent": "DATA_QUALITY_REVIEW"},
            {"q": "how does my RB room look?", "intent": "POSITION_REVIEW"},
            {"q": "rank my WRs", "intent": "POSITION_RANKING"},
            {"q": "give me a 3 step plan", "intent": "GM_PLAN"},
        ],
    }

    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_UNDERSTANDING_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "Return strict JSON only. No prose."},
                {"role": "user", "content": json.dumps(prompt)},
            ],
            temperature=0,
        )

        data = json.loads(resp.choices[0].message.content or "{}")
        if data.get("intent") not in CANONICAL_INTENTS:
            return _rules_fallback(question)

        data["source"] = "openai"
        return data

    except Exception:
        return _rules_fallback(question)
