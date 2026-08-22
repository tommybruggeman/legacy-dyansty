from __future__ import annotations

from gm_assistant.nlu.parser import parse_gm_question
from gm_assistant.planner.reasoning_planner import build_execution_plan
from gm_assistant.executor.executor import execute_plan
from gm_assistant.composer.contract_composer import compose_contract_value_answer


def answer_contract_value(question: str, owner_team_name: str) -> dict:
    parsed = parse_gm_question(question)
    plan = build_execution_plan(parsed)
    execution = execute_plan(plan, question=question, owner_team_name=owner_team_name)

    data = execution.get("rank_contract_values") or {}

    if not data:
        return {
            "answer_type": "contract_value_answer",
            "intent": parsed.intent,
            "decision": "CONTRACT_RANKING_INCOMPLETE",
            "summary": "I know this is a contract-value question, but I could not rank the contracts yet.",
            "execution": execution,
        }

    return {
        "answer_type": "contract_value_answer",
        "intent": parsed.intent,
        "decision": "RANK_CONTRACT_VALUES",
        "summary": compose_contract_value_answer(data, question=question),
        "execution": execution,
    }
