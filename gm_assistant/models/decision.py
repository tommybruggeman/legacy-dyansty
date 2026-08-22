from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrainRoute:
    intent: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    required_context: list[str] = field(default_factory=list)
    answer_shape: str | None = None
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class BrainDecision:
    decision_type: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    missing_context: list[str] = field(default_factory=list)
    reasoning_notes: list[str] = field(default_factory=list)


@dataclass
class BrainAnswer:
    decision: str
    summary: str
    route: dict[str, Any]
    validation: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
