from dataclasses import dataclass, field


@dataclass
class DecisionOption:
    title: str
    recommendation: str

    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    upside: str = ""
    downside: str = ""

    confidence: float = 0.0


@dataclass
class BrainDecision:

    decision_type: str

    options: list[DecisionOption] = field(default_factory=list)

    overall_take: str = ""

    missing_context: list[str] = field(default_factory=list)
