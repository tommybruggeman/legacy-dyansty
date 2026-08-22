from __future__ import annotations

import re
from gm_assistant.response_engine.models import UserIntent


WIN_NOW_TERMS = [
    "win the championship",
    "win championship",
    "win now",
    "contend",
    "title this year",
    "championship this year",
    "all in",
]


def classify_intent(question: str) -> UserIntent:
    q = question.lower().strip()

    if any(term in q for term in WIN_NOW_TERMS):
        return UserIntent(
            raw_question=question,
            intent_type="team_goal_update",
            team_goal="win_now",
            asks_for="acknowledge_and_apply_goal",
            should_update_state=True,
        )

    if "contract" in q and any(x in q for x in ["hurt", "bad", "worst", "problem"]):
        return UserIntent(
            raw_question=question,
            intent_type="contract_diagnosis",
            asks_for="identify_contract_liabilities",
        )

    if any(x in q for x in ["trade", "shop", "move", "sell"]):
        subject = extract_player_name(question)
        return UserIntent(
            raw_question=question,
            intent_type="player_trade_decision",
            subject=subject,
            asks_for="decision",
        )

    if any(x in q for x in ["drop", "cut", "release"]):
        subject = extract_player_name(question)
        return UserIntent(
            raw_question=question,
            intent_type="drop_decision",
            subject=subject,
            asks_for="decision",
        )

    if any(x in q for x in ["what should i do", "team look", "strategy", "plan"]):
        return UserIntent(
            raw_question=question,
            intent_type="team_strategy",
            asks_for="strategy",
        )

    return UserIntent(
        raw_question=question,
        intent_type="general_gm_question",
        asks_for="direct_answer",
    )


def extract_player_name(question: str) -> str | None:
    cleaned = re.sub(r"[^a-zA-Z .'-]", " ", question)
    words = cleaned.split()

    stop = {
        "should", "trade", "shop", "move", "sell", "drop", "cut", "release",
        "i", "my", "the", "a", "an", "for", "to", "get", "rid", "of"
    }

    candidates = [w for w in words if w.lower() not in stop]

    if len(candidates) >= 2:
        return " ".join(candidates[-2:])

    if candidates:
        return candidates[-1]

    return None
