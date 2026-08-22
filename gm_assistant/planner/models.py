from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# -------------------------------------------------------------------
# Legacy planner compatibility models
# Some older modules import ExecutionPlan / ExecutionStep.
# Keep these so existing capability code does not break.
# -------------------------------------------------------------------

@dataclass
class ExecutionStep:
    name: str
    capability: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


@dataclass
class ExecutionPlan:
    intent: str
    question: str
    steps: list[ExecutionStep] = field(default_factory=list)
    confidence: float = 0.75
    metadata: dict[str, Any] = field(default_factory=dict)


# -------------------------------------------------------------------
# Planner v1 models
# -------------------------------------------------------------------

@dataclass
class PlanTask:
    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class GMPlan:
    intent: str
    question: str
    tasks: list[PlanTask]
    route_hint: str | None = None
    confidence: float = 0.75

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "question": self.question,
            "tasks": [{"name": t.name, "params": t.params} for t in self.tasks],
            "route_hint": self.route_hint,
            "confidence": self.confidence,
        }


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
