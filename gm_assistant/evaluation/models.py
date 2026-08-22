from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    category: str
    user_question: str
    expected_answer_type: str
    required_facts: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    expected_constraint_handling: tuple[str, ...] = ()
    openai_eligible: bool = False
    expected_fallback_behavior: str = "deterministic"
    scoring_criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationScore:
    factual_grounding: str
    constraint_compliance: str
    recommendation_usefulness: str
    missing_evidence_handling: str
    hallucination: str
    owner_goal_alignment: str
    structural_football_reasoning: str
    safety: str
    passed: bool
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationResult:
    case: EvaluationCase
    deterministic_fallback: str
    rendered_answer: str
    provider_called: bool
    provider_call_count: int
    response_type: str | None
    validation_status: str | None
    fallback_status: str | None
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    comparison: str = "neutral"
    score: EvaluationScore | None = None
    reviewer_notes: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationSuiteResult:
    suite_id: str
    results: tuple[EvaluationResult, ...] = field(default_factory=tuple)
    live: bool = False

    @property
    def total_cases(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.score and result.score.passed)

    @property
    def failed(self) -> int:
        return self.total_cases - self.passed

    @property
    def improved(self) -> int:
        return sum(1 for result in self.results if result.comparison == "improved")

    @property
    def neutral(self) -> int:
        return sum(1 for result in self.results if result.comparison == "neutral")

    @property
    def regressed(self) -> int:
        return sum(1 for result in self.results if result.comparison == "regressed")

    @property
    def hallucination_failures(self) -> int:
        return sum(1 for result in self.results if result.score and result.score.hallucination not in {"none_detected", "not_applicable"})

    @property
    def safety_failures(self) -> int:
        return sum(1 for result in self.results if result.score and result.score.safety == "fail")

    @property
    def total_tokens(self) -> int:
        return sum(result.total_tokens or 0 for result in self.results)

    def to_payload(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "live": self.live,
            "total_cases": self.total_cases,
            "passed": self.passed,
            "failed": self.failed,
            "improved": self.improved,
            "neutral": self.neutral,
            "regressed": self.regressed,
            "hallucination_failures": self.hallucination_failures,
            "safety_failures": self.safety_failures,
            "total_tokens": self.total_tokens,
            "results": [result.to_payload() for result in self.results],
        }
