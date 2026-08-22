from __future__ import annotations


def route_question(question: str) -> str:
    q = question.lower()

    if "rookie" in q or "draft" in q or "prospect" in q:
        return "ROOKIE_BOARD"

    if "trade" in q:
        return "TRADE_ANALYSIS"

    if "contract" in q or "drop" in q or "cut" in q:
        return "CONTRACT_DECISION"

    if "team" in q or "roster" in q:
        return "TEAM_STRATEGY"

    return "GENERAL_GM"
