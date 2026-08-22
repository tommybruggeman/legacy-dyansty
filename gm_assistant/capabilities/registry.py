from __future__ import annotations

from gm_assistant.capabilities.contract_value import answer_contract_value
from gm_assistant.capabilities.target_recommendations import answer_target_recommendations


CAPABILITY_HANDLERS = {
    "contract_value_ranking": answer_contract_value,
    "target_recommendations": answer_target_recommendations,
    # free_agent_targets now routes through reasoning/evidence.py using player_brain_context
}


def run_capability(intent: str, question: str, owner_team_name: str) -> dict | None:
    handler = CAPABILITY_HANDLERS.get(intent)
    if not handler:
        return None
    return handler(question, owner_team_name)
