from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserIntent:
    raw_question: str
    intent_type: str
    subject: str | None = None
    team_goal: str | None = None
    asks_for: str | None = None
    should_update_state: bool = False


@dataclass
class Recommendation:
    action: str
    confidence: int
    priority: str
    thesis: str
    reason_codes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    next_action: str | None = None


@dataclass
class ResponsePlan:
    opening: str
    body_points: list[str]
    caveat: str | None = None
    next_action: str | None = None
    forbidden_repeats: list[str] = field(default_factory=list)
    max_paragraphs: int = 5
