from __future__ import annotations

import json
from typing import Any, Dict

from gm_assistant.understanding.openai_client import get_openai_client


VALID_INTENTS = {
    "TEAM_REVIEW",
    "TEAM_STRENGTHS",
    "IDENTIFY_NEEDS",
    "CORE_PLAYER_REVIEW",
    "ROSTER_EXIT_DECISION",
    "PLAYER_TRADE_DECISION",
    "PLAYER_HOLD_DECISION",
    "PLAYER_CUT_DECISION",
    "PLAYER_CONTRACT_FIT",
    "TRADE_STRATEGY",
    "TRADE_PACKAGE",
    "TRADE_RETURN_VALUE",
    "POSITION_REVIEW",
    "CONTRACT_AUDIT",
    "CONTRACT_BEST_VALUE",
    "PRODUCTION_REVIEW",
    "DATA_QUALITY_REVIEW",
    "FREE_AGENT_TARGETS",
    "ROOKIE_DRAFT_PICK_DECISION",
    "ROOKIE_BOARD_REVIEW",
    "CUT_OR_CHURN",
    "SNEAKY_HOLD",
    "SELL_HIGH",
    "GM_PLAN",
    "GM_SUMMARY",
    "BLIND_SPOT",
    "QUESTION_RECOMMENDATION",
    "UNKNOWN",
}


SYSTEM_PROMPT = """
You are the intent understanding layer for a dynasty fantasy football GM assistant.

Return ONLY valid JSON. No markdown.

Your job is to classify the user's question into a routing object.

Important:
- Extract player names when present.
- Extract positions when present: QB, RB, WR, TE.
- Do not make roster recommendations.
- Do not answer the user.
- Only classify the question.
- If the question asks about one named player, scope should be "single_player".
- If the question asks about a position group, scope should be "position".
- If the question asks for plan, next move, summary, blind spot, or what question to ask, use GM strategy intents.
"""


def _safe_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        return {}


def _normalize(result: Dict[str, Any], question: str) -> Dict[str, Any]:
    intent = str(result.get("intent") or "UNKNOWN").upper()
    if intent not in VALID_INTENTS:
        intent = "UNKNOWN"

    players = result.get("players") or []
    positions = result.get("positions") or []

    if not isinstance(players, list):
        players = []
    if not isinstance(positions, list):
        positions = []

    try:
        confidence = float(result.get("confidence", 0.0))
    except Exception:
        confidence = 0.0

    confidence = max(0.0, min(confidence, 1.0))

    return {
        "intent": intent,
        "domain": result.get("domain") or "general",
        "players": [str(p).strip() for p in players if str(p).strip()],
        "positions": [str(p).upper().strip() for p in positions if str(p).strip()],
        "action": result.get("action") or None,
        "scope": result.get("scope") or "team",
        "confidence": confidence,
        "source": "ai_intent_adapter",
        "question": question,
        "route_hint": result.get("route_hint") or intent.lower(),
        "needs_player_lookup": bool(result.get("needs_player_lookup", False)),
        "is_rookie_question": bool(result.get("is_rookie_question", False)),
        "is_comparison": bool(result.get("is_comparison", False)),
        "is_value_question": bool(result.get("is_value_question", False)),
    }


def understand_with_ai(question: str) -> Dict[str, Any] | None:
    client = get_openai_client()
    if client is None:
        return None

    user_prompt = f"""
Classify this GM assistant question.

Question:
{question}

Return JSON with this shape:
{{
  "intent": "PLAYER_TRADE_DECISION",
  "domain": "player",
  "players": ["Bryce Young"],
  "positions": [],
  "action": "trade",
  "scope": "single_player",
  "confidence": 0.92,
  "route_hint": "player_trade_decision",
  "needs_player_lookup": true,
  "is_rookie_question": false,
  "is_comparison": false,
  "is_value_question": false
}}
"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )

        raw = resp.choices[0].message.content or "{}"
        return _normalize(_safe_json(raw), question)

    except Exception as e:
        return {
            "intent": "UNKNOWN",
            "domain": "general",
            "players": [],
            "positions": [],
            "confidence": 0.0,
            "source": "ai_intent_adapter_error",
            "question": question,
            "error": str(e),
        }


if __name__ == "__main__":
    tests = [
        "should I trade Bryce Young?",
        "what should I ask for Garrett Wilson?",
        "give me a 3 step plan",
        "who is a sell high?",
        "who is overpaid?",
        "rank my WRs",
        "who has fallback production data?",
    ]

    for q in tests:
        print("\nQUESTION:", q)
        print(understand_with_ai(q))
