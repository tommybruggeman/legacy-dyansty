from __future__ import annotations

from gm_assistant.nlu.parser import parse_gm_question
from gm_assistant.planner.reasoning_planner import build_execution_plan
from gm_assistant.executor.executor import execute_plan
from gm_assistant.composer.target_composer import compose_target_recommendations


def answer_target_recommendations(question: str, owner_team_name: str) -> dict:
    parsed = parse_gm_question(question)
    plan = build_execution_plan(parsed)
    execution = execute_plan(plan, question=question, owner_team_name=owner_team_name)

    targets = execution.get("rank_targets") or []

    if not targets:
        return {
            "answer_type": "target_recommendation_answer",
            "intent": parsed.intent,
            "decision": "TARGET_SCAN_INCOMPLETE",
            "summary": "I know the right target profile, but I could not rank actual targets yet.",
            "execution": execution,
        }

    position = parsed.positions[0] if parsed.positions else "RB"

    return {
        "answer_type": "target_recommendation_answer",
        "intent": parsed.intent,
        "decision": "RANK_TARGETS",
        "summary": compose_target_recommendations(targets, position=position),
        "execution": execution,
    }
