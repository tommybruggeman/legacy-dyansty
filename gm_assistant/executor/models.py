from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapabilityResult:
    name: str
    success: bool
    data: Any = None
    message: str | None = None


@dataclass
class ExecutionResult:
    results: list[CapabilityResult] = field(default_factory=list)

    def get(self, name: str):
        for r in self.results:
            if r.name == name:
                return r.data
        return None

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)
