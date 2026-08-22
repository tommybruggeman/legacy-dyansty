from __future__ import annotations

from gm_assistant.nlu.parser import parse_gm_question
from gm_assistant.reasoning.models import QuestionAnalysis


INTENT_MAP = {
    "player_trade_decision": "player_decision",
    "player_drop_decision": "player_decision",
}


def analyze_question(question: str) -> QuestionAnalysis:
    parsed = parse_gm_question(question)

    intent = INTENT_MAP.get(parsed.intent, parsed.intent)
    decision_type = parsed.decision_type

    if parsed.intent == "player_trade_decision":
        decision_type = "trade"
    if parsed.intent == "player_drop_decision":
        decision_type = "drop"

    return QuestionAnalysis(
        raw_question=question,
        intent=intent,
        decision_type=decision_type,
        player_name=parsed.player_names[0] if parsed.player_names else None,
        goal=parsed.team_goal,
        needs_player_lookup=parsed.needs_player_lookup,
        needs_contracts=parsed.needs_contracts,
        needs_market=parsed.needs_market,
        needs_team_fit=parsed.needs_team_fit,
        needs_roster=parsed.needs_roster,
        update_state=parsed.intent == "change_team_goal",
        confidence=parsed.confidence,
    )
