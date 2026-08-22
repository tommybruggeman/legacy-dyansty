from __future__ import annotations

from gm_assistant.engines.base import BaseDecisionEngine


class ContractEngine(BaseDecisionEngine):
    name = "contract_engine"

    def can_handle(self, route: dict) -> float:
        return 1.0 if route.get("intent") == "contract_pain_analysis" else 0.0

    def required_context(self, route: dict) -> list[str]:
        return ["player_universe", "contract_context", "team_future_context"]

    def execute(self, question: str, owner_team_name: str, route: dict) -> dict:
        return {
            "decision": "CONTRACT_PAIN_ANALYSIS",
            "summary": "Contract question routed correctly. Next step is wiring this engine to the existing contract value ranking logic.",
            "confidence": 0.7,
            "data": {"engine": self.name, "placeholder": True},
            "missing_context": [],
        }
