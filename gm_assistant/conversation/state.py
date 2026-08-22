from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


PLAYER_PATTERNS = [
    r"garrett wilson",
    r"breece hall",
    r"josh allen",
    r"jared goff",
    r"brandon aiyuk",
    r"omarion hampton",
    r"ashton jeanty",
    r"josh jacobs",
    r"isiah pacheco",
    r"bryce young",
    r"justin fields",
]


@dataclass
class ConversationState:
    owner_team_name: str
    current_player: str | None = None
    compared_player: str | None = None
    current_topic: str | None = None
    team_goal: str | None = None
    last_recommendation: str | None = None
    last_answer: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def update_from_question(self, question: str) -> None:
        q = question.lower()

        for pattern in PLAYER_PATTERNS:
            if re.search(pattern, q):
                name = pattern.title()
                if self.current_player and name != self.current_player:
                    self.compared_player = name
                else:
                    self.current_player = name

        if any(x in q for x in ["trade", "deal", "offer", "counter"]):
            self.current_topic = "trade"

        if any(x in q for x in ["cut", "drop", "release"]):
            self.current_topic = "cut"

        if any(x in q for x in ["why", "explain"]):
            self.current_topic = self.current_topic or "explain"

        if any(x in q for x in ["win now", "win-now", "contending", "contender", "contend"]):
            self.team_goal = "contend"

        if any(x in q for x in ["rebuild", "rebuilding", "future", "picks"]):
            self.team_goal = "rebuild"

        if any(x in q for x in ["retool", "soft reset"]):
            self.team_goal = "retool"

    def resolve_followup(self, question: str) -> str:
        q = question.strip()
        q_lower = q.lower()

        player = self.current_player
        compared = self.compared_player
        goal = self.team_goal

        if player:
            q = re.sub(r"\bhim\b", player, q, flags=re.I)
            q = re.sub(r"\bhe\b", player, q, flags=re.I)
            q = re.sub(r"\bhis\b", f"{player}'s", q, flags=re.I)

        if q_lower in {"why", "why?", "explain", "explain why"}:
            if player:
                q = f"Explain the previous recommendation on {player}."
            else:
                q = "Explain the previous recommendation."

        if q_lower.startswith("what if") and player and compared:
            q = (
                f"Evaluate this hypothetical trade scenario for {self.owner_team_name}: "
                f"give {player}, receive {compared}. "
                f"Consider contract, salary, years, roster fit, team goal, risk, and win-now impact."
            )

        if "does that change" in q_lower and player:
            q = (
                f"Re-evaluate {player} with this changed team goal/context: {goal or 'unspecified'}. "
                f"Explain whether the recommendation changes from the previous answer."
            )

        if "would you cut" in q_lower and player:
            q = (
                f"Evaluate whether {self.owner_team_name} should cut/drop {player} if no trade market exists. "
                f"Compare hold vs trade vs cut/release. Include contract, dead-cap implications if available, "
                f"replacement value, and team goal: {goal or 'unspecified'}."
            )

        if "worth his contract" in q_lower and player:
            q = (
                f"What would need to happen for {player} to become worth his current contract? "
                f"Include required production tier, expected PPG, positional finish, contract efficiency, "
                f"risk, and probability-style confidence."
            )

        if goal:
            q += f"\n\nTeam goal/context: {goal}."

        if self.last_answer:
            q += f"\n\nPrevious assistant answer:\n{self.last_answer}"

        return q

    def record_turn(self, question: str, resolved_question: str, answer: Any) -> None:
        answer_text = answer if isinstance(answer, str) else str(answer)

        self.last_answer = answer_text

        if "HOLD" in answer_text.upper():
            self.last_recommendation = "hold"
        elif "SELL" in answer_text.upper():
            self.last_recommendation = "sell"
        elif "SHOP" in answer_text.upper():
            self.last_recommendation = "shop"
        elif "CUT" in answer_text.upper() or "DROP" in answer_text.upper():
            self.last_recommendation = "cut"

        self.history.append({
            "question": question,
            "resolved_question": resolved_question,
            "answer": answer,
            "current_player": self.current_player,
            "compared_player": self.compared_player,
            "current_topic": self.current_topic,
            "team_goal": self.team_goal,
            "last_recommendation": self.last_recommendation,
        })
