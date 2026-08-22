from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class SkillResult:
    decision: str
    summary: str
    confidence: float = 0.75
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "summary": self.summary,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "metadata": self.metadata,
        }


SkillHandler = Callable[[str, str, dict], dict]
