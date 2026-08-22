from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidencePack:
    question: str
    owner_team_name: str
    understanding: dict
    player: dict[str, Any] | None = None
    roster: list[dict[str, Any]] = field(default_factory=list)
    team_context: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class ReasoningResult:
    decision: str
    recommendation: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "risks": self.risks,
            "actions": self.actions,
            "evidence": self.evidence,
        }
