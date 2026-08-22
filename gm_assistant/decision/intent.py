from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class GMIntent:
    intent: str
    owner_team_name: str
    primary_player: str | None = None
    comparison_player: str | None = None
    team_goal: str | None = None
    topic: str | None = None
    original_question: str | None = None
    resolved_question: str | None = None
    previous_recommendation: str | None = None

    def to_dict(self):
        return asdict(self)
