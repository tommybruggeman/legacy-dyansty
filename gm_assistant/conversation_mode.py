from __future__ import annotations

import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().strip())


def classify_conversation_mode(question: str) -> str:
    q = normalize(question)

    # CREATE: user wants an actual constructed solution.
    if any(x in q for x in [
        "build me", "create", "construct", "make me", "give me a package",
        "trade package", "proposal", "offer", "counter", "4 team", "3 team",
        "multi-team", "multi team"
    ]):
        return "create"

    # ANALYZE: user wants action/advice.
    if any(x in q for x in [
        "what should i do", "should i", "would you", "recommend",
        "best move", "next move", "who should i", "trade him", "drop him",
        "cut him", "hold him", "sell him", "buy him", "start", "sit"
    ]):
        return "analyze"

    # EXPLAIN: user wants why/how.
    if any(x in q for x in [
        "why", "how come", "explain", "walk me through", "break down",
        "what makes", "what caused", "where is this coming from"
    ]):
        return "explain"

    # OPINION: user wants a take, not an action.
    if any(x in q for x in [
        "what do you think", "how do you feel", "is he good",
        "is this good", "is this bad", "does this suck", "do you like",
        "thoughts on", "opinion", "is he worth", "worth it",
        "is the contract good", "is the contract bad", "good contract",
        "bad contract"
    ]):
        return "opinion"

    return "opinion"
