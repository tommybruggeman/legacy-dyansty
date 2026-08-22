from __future__ import annotations

from abc import ABC, abstractmethod
from gm_assistant.models.evidence import Evidence


class EvidenceProvider(ABC):
    """
    Base class for every evidence provider.
    Each provider contributes evidence from one domain.
    """

    name = "base"

    @abstractmethod
    def collect(
        self,
        question: str,
        candidate: dict,
        owner_team_name: str,
        context: dict,
    ) -> list[Evidence]:
        raise NotImplementedError
