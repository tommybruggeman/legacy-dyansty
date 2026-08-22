from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QuestionAnalysis:
    raw_question: str
    intent: str
    decision_type: str | None = None
    player_name: str | None = None
    goal: str | None = None
    needs_player_lookup: bool = False
    needs_contracts: bool = False
    needs_market: bool = False
    needs_team_fit: bool = False
    needs_roster: bool = False
    update_state: bool = False
    confidence: float = 0.7


@dataclass
class BrainState:
    owner_team_name: str
    team_goal: str | None = None
    current_player: str | None = None
    current_topic: str | None = None


@dataclass
class EvidenceBundle:
    player: dict[str, Any] = field(default_factory=dict)
    facts: list[dict[str, Any]] = field(default_factory=list)
    team_needs: list[str] = field(default_factory=list)
    roster: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BrainDecision:
    action: str
    confidence: float
    thesis: str
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    next_action: str | None = None
