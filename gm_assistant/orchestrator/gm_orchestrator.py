from gm_assistant.orchestrator.intent_router import route_intent
from gm_assistant.evaluation.answer_validator import validate_answer

def orchestrate_question(question: str, owner_name: str | None = None) -> dict:
    route = route_intent(question, owner_name)

    return {
        "question": question,
        "owner_name": owner_name,
        "route": route,
        "status": "ROUTED",
    }

def validate_orchestrated_answer(question: str, owner_name: str, answer: str) -> dict:
    route = route_intent(question, owner_name)
    validation = validate_answer(question, route, answer)

    return {
        "route": route,
        "validation": validation,
    }
