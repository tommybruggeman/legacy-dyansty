from __future__ import annotations

import re


def classify_intent(question: str, current_player: str | None = None) -> str:
    q = question.lower().strip()

    if any(x in q for x in ["drop", "cut", "keep", "release"]):
        return "cut_decision"

    if any(x in q for x in ["hurt", "hurting", "problem", "bad contract", "holding me back"]):
        return "roster_liability"

    if any(x in q for x in ["market-check", "market check", "shop", "trade first"]):
        return "market_check"

    if any(x in q for x in ["worth", "justify"] ) and "contract" in q:
        return "contract_forecast"

    if any(x in q for x in ["what if", "could get", "for"]):
        if current_player:
            return "trade_scenario"

    if any(x in q for x in ["next move", "what should i do next", "first move"]):
        return "next_move"

    if any(x in q for x in ["how does team look", "how does my team look", "team look", "roster look"]):
        return "team_strategy"

    if any(x in q for x in ["why", "explain"]):
        return "explain_previous"

    return "general"
