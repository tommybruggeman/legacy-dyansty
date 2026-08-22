from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class GMConversationState:
    owner_team_name: str
    current_player: str | None = None
    compared_player: str | None = None
    current_topic: str | None = None
    team_goal: str | None = None
    last_intent: str | None = None
    last_recommendation: str | None = None
    turns: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["turns"] = d.get("turns") or []
        return d


def _extract_player_from_answer(answer: dict[str, Any]) -> str | None:
    p = answer.get("player")
    if isinstance(p, dict):
        return p.get("player_name") or p.get("player")

    return answer.get("player_name")


def update_conversation_state(
    state: dict[str, Any] | None,
    question: str,
    answer: dict[str, Any],
    owner_team_name: str,
) -> dict[str, Any]:
    s = GMConversationState(**(state or {"owner_team_name": owner_team_name}))

    q = question.lower()
    intent = answer.get("intent") or answer.get("answer_type")
    player = _extract_player_from_answer(answer)

    if player:
        s.current_player = player

    if intent:
        s.last_intent = intent

    if any(x in q for x in ["trade", "shop", "sell"]):
        s.current_topic = "trade"
    elif any(x in q for x in ["cut", "drop", "release"]):
        s.current_topic = "cut"
    elif any(x in q for x in ["rebuild", "retool", "contend", "win"]):
        s.current_topic = "team_direction"
    elif intent:
        s.current_topic = str(intent)

    rec = answer.get("recommendation")
    if rec:
        s.last_recommendation = rec

    turns = s.turns or []
    turns.append({
        "question": question,
        "intent": intent,
        "player": player,
        "summary": (answer.get("summary") or "")[:500],
    })

    s.turns = turns[-8:]

    return s.to_dict()


def resolve_followup_question(question: str, state: dict[str, Any] | None) -> str:
    if not state:
        return question

    q = question.strip()
    q_lower = q.lower()

    current_player = state.get("current_player")
    current_topic = state.get("current_topic")

    pronoun_followup = any(x in q_lower for x in [
        "him",
        "he",
        "that player",
        "that guy",
        "his contract",
        "his value",
    ])

    if current_player and pronoun_followup:
        q = re.sub(r"\bhim\b", current_player, q, flags=re.I)
        q = re.sub(r"\bhe\b", current_player, q, flags=re.I)
        q = re.sub(r"\bhis\b", f"{current_player}'s", q, flags=re.I)
        q = re.sub(r"that player|that guy", current_player, q, flags=re.I)

    if current_player and q_lower in {"why", "why?", "explain", "explain more", "tell me more"}:
        return f"Explain why your last recommendation about {current_player} makes sense."

    if current_player and q_lower in {"what about his contract?", "contract?", "and the contract?", "what about contract?"}:
        return f"What should I think about {current_player}'s contract?"

    if current_player and q_lower in {"what would you take?", "what should i ask for?", "what return?", "what's the price?"}:
        return f"If I trade {current_player}, what should I ask for?"

    if current_topic == "trade" and current_player and "instead" in q_lower:
        return f"Compare that trade idea against holding {current_player}."

    return q
