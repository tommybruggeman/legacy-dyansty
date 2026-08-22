from gm_assistant.orchestrator.intent_registry import INTENT_REGISTRY
from gm_assistant.orchestrator.entity_extractor import extract_entities

def route_intent(question: str, default_team: str | None = None) -> dict:
    q = question.lower()
    entities = extract_entities(question, default_team)

    # High-confidence phrase overrides before generic keyword scoring
    if any(x in q for x in ["fa", "fas", "free agent", "waiver", "available players"]):
        return {
            "intent": "free_agent_targets",
            "confidence": 0.95,
            "entities": entities,
            "required_context": INTENT_REGISTRY["free_agent_targets"]["required_context"],
            "answer_shape": INTENT_REGISTRY["free_agent_targets"]["answer_shape"],
            "scores": {},
        }

    scores = {}

    for intent, cfg in INTENT_REGISTRY.items():
        score = 0

        for kw in cfg["keywords"]:
            if kw in q:
                score += 1

        if entities.get("pick") and "rookie" in intent:
            score += 4

        if "draft" in q and entities.get("pick"):
            score += 5

        if "who should i take" in q or "who should i target" in q:
            if entities.get("pick"):
                score += 3

        scores[intent] = score

    best_intent = max(scores, key=scores.get)
    confidence = min(0.99, scores[best_intent] / 8) if scores[best_intent] else 0.25

    if scores[best_intent] == 0:
        best_intent = "team_direction"

    cfg = INTENT_REGISTRY[best_intent]

    return {
        "intent": best_intent,
        "confidence": round(confidence, 2),
        "entities": entities,
        "required_context": cfg["required_context"],
        "answer_shape": cfg["answer_shape"],
        "scores": scores,
    }
