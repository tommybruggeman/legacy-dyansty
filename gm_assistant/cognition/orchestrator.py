from __future__ import annotations

from gm_assistant.orchestrator.intent_router import route_intent
from gm_assistant.evaluation.answer_validator import validate_answer
from gm_assistant.cognition.registry import EngineRegistry


class GMBrainOrchestrator:
    def __init__(self):
        self.registry = EngineRegistry()

    def think(self, question: str, owner_team_name: str) -> dict:
        route = route_intent(question, owner_team_name)
        engine = self.registry.select(route)

        if not engine:
            return {
                "handled": False,
                "route": route,
                "reason": "No engine selected.",
            }

        result = engine.execute(question, owner_team_name, route)

        answer = {
            "decision": result.get("decision"),
            "summary": result.get("summary"),
            "confidence": result.get("confidence", route.get("confidence", 0.0)),
            "route": route,
            "data": result.get("data", {}),
            "missing_context": result.get("missing_context", []),
        }

        validation = validate_answer(question, route, answer.get("summary", ""))
        answer["validation"] = validation

        # Only engines that produce real answers should take over the live app.
        # Placeholder engines can route correctly but should fall back to existing gm_brain.py logic.
        is_placeholder = result.get("data", {}).get("placeholder", False)

        answer["handled"] = validation.get("passed", False) and not is_placeholder

        return answer
