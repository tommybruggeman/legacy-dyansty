from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class BrainContext:
    owner_name: str
    question: str
    conversation_state: Optional[Dict[str, Any]] = field(default_factory=dict)

    team_context: Dict[str, Any] = field(default_factory=dict)
    player_context: Dict[str, Any] = field(default_factory=dict)
    contract_context: Dict[str, Any] = field(default_factory=dict)
    league_context: Dict[str, Any] = field(default_factory=dict)
    goal_context: Dict[str, Any] = field(default_factory=dict)
    available_engines: Dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "owner_name": self.owner_name,
            "question": self.question,
            "conversation_state": self.conversation_state or {},
            "team_context": self.team_context,
            "player_context": self.player_context,
            "contract_context": self.contract_context,
            "league_context": self.league_context,
            "goal_context": self.goal_context,
            "available_engines": self.available_engines,
        }


def build_brain_context(
    question: str,
    owner_name: str,
    conversation_state: Optional[Dict[str, Any]] = None,
) -> BrainContext:
    """
    Single shared object every future decision layer should receive.

    This prevents new engines from bypassing the intelligence already built
    into the current GM brain.
    """

    state = conversation_state or {}

    return BrainContext(
        owner_name=owner_name,
        question=question,
        conversation_state=state,
        goal_context={
            "team_goal": state.get("team_goal"),
            "last_recommendation": state.get("last_recommendation"),
            "current_topic": state.get("current_topic"),
        },
        available_engines={
            "current_gm_brain": True,
            "rookie_decision_engine": False,
            "trade_construction_engine": False,
            "league_intelligence_engine": False,
            "conversation_intelligence_engine": False,
        },
    )
