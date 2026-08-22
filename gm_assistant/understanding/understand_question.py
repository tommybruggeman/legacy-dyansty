from __future__ import annotations

from typing import Any, Dict

from gm_assistant.understanding.local_intent_adapter import understand_locally

try:
    from gm_assistant.understanding.ai_intent_adapter import understand_with_ai
except Exception:
    understand_with_ai = None


AI_CONFIDENCE_MIN = 0.70
LOCAL_CONFIDENCE_MIN = 0.70


def understand_question(question: str) -> Dict[str, Any]:
    local = understand_locally(question)

    if local.get("confidence", 0) >= LOCAL_CONFIDENCE_MIN:
        return local

    if understand_with_ai:
        ai = understand_with_ai(question)
        if ai and ai.get("confidence", 0) >= AI_CONFIDENCE_MIN:
            return ai

    return local


if __name__ == "__main__":
    tests = [
        "should I trade Bryce Young?",
        "what should I ask for Garrett Wilson?",
        "give me a 3 step plan",
        "who is a sell high?",
        "who is overpaid?",
        "rank my WRs",
        "who has fallback production data?",
        "what question should I be asking?",
    ]

    for q in tests:
        print("\nQUESTION:", q)
        print(understand_question(q))
