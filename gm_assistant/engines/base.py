from __future__ import annotations

from abc import ABC, abstractmethod


class BaseDecisionEngine(ABC):
    name = "base"

    @abstractmethod
    def can_handle(self, route: dict) -> float:
        pass

    @abstractmethod
    def required_context(self, route: dict) -> list[str]:
        pass

    @abstractmethod
    def execute(self, question: str, owner_team_name: str, route: dict) -> dict:
        pass
