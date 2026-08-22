from __future__ import annotations

from gm_assistant.response_engine.intent import classify_intent
from gm_assistant.response_engine.models import Recommendation
from gm_assistant.response_engine.planner import build_response_plan
from gm_assistant.response_engine.writer import write_coach_response


def answer_with_response_engine(question: str, recommendation: Recommendation | None = None) -> str:
    intent = classify_intent(question)
    plan = build_response_plan(intent, recommendation)
    return write_coach_response(plan)
