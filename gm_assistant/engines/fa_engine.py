from __future__ import annotations

from gm_assistant.engines.base import BaseDecisionEngine


class FreeAgentEngine(BaseDecisionEngine):
    name = "free_agent_engine"

    def can_handle(self, route: dict) -> float:
        return 1.0 if route.get("intent") == "free_agent_targets" else 0.0

    def required_context(self, route: dict) -> list[str]:
        return ["player_universe", "team_future_context", "roster_strength_context"]

    def execute(self, question: str, owner_team_name: str, route: dict) -> dict:
        return {
            "decision": "FREE_AGENT_TARGETS",
            "summary": "Free agent question routed correctly. Next step is wiring this engine to the existing FA target logic.",
            "confidence": 0.7,
            "data": {"engine": self.name, "placeholder": True},
            "missing_context": [],
        }
