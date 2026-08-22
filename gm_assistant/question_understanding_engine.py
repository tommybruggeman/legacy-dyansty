from __future__ import annotations

import re
from auth import service_client
from gm_assistant.ai_intent_router import classify_intent
from gm_assistant.ai_understanding import understand_question_ai
from gm_assistant.understanding.local_intent_adapter import understand_locally


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


_KNOWN_PLAYERS_CACHE = None


def _fetch_known_players():
    global _KNOWN_PLAYERS_CACHE

    if _KNOWN_PLAYERS_CACHE is not None:
        return _KNOWN_PLAYERS_CACHE

    sb = service_client()

    # Graph first. This is the unified identity source.
    rows = (
        sb.table("player_graph_v2")
        .select("player_name")
        .limit(2000)
        .execute()
        .data
        or []
    )

    if not rows:
        rows = (
            sb.table("player_universe")
            .select("player_name")
            .limit(2000)
            .execute()
            .data
            or []
        )

    names = sorted(
        {
            str(r.get("player_name") or "").strip()
            for r in rows
            if r.get("player_name")
        },
        key=len,
        reverse=True,
    )

    _KNOWN_PLAYERS_CACHE = names
    return _KNOWN_PLAYERS_CACHE

def extract_players(question: str) -> list[dict]:
    q = _clean(question)
    found = []

    for player_name in _fetch_known_players():
        name = str(player_name or "").strip()
        if not name:
            continue

        if _clean(name) in q:
            found.append({
                "_player_name": name,
                "player_name": name,
            })

    return found

def _finalize_understanding(question: str, understanding: dict, player_names: list[str]) -> dict:
    q = _clean(question)

    if player_names:
        understanding["players"] = player_names

    understanding["question"] = question
    understanding["player_count"] = len(understanding.get("players") or [])
    understanding["is_rookie_question"] = understanding.get("intent") in {
        "ROOKIE_DRAFT_PICK_DECISION",
        "ROOKIE_PLAYER_DECISION",
        "ROOKIE_PLAYER_COMPARISON",
        "ROOKIE_POSITION_VALUE",
    } or any(w in q for w in ["rookie", "draft", "1.01", "1.02", "1.03"])

    understanding["is_comparison"] = (
        len(understanding.get("players") or []) >= 2
        or any(w in q for w in [" vs ", "versus", "compare", "better than", "or "])
    )
    understanding["is_value_question"] = any(
        w in q for w in ["value", "best value", "undervalued", "underpaid", "worth"]
    )

    return understanding


def understand_question(question: str) -> dict:
    known_players = _fetch_known_players()
    players = extract_players(question)
    player_names = [p["_player_name"] for p in players]

    # 1. Fast local understanding first.
    local = understand_locally(question)
    if player_names:
        local["players"] = player_names
        local["needs_player_lookup"] = True
        if local.get("scope") == "team":
            local["scope"] = "single_player"

    if float(local.get("confidence") or 0) >= 0.70 and local.get("intent") != "UNKNOWN":
        return _finalize_understanding(question, local, player_names)

    # 2. Existing AI/rules classifier second.
    known_players_csv = ",".join(known_players[:300])
    ai = classify_intent(question, known_players_csv)

    return _finalize_understanding(question, ai, player_names)


if __name__ == "__main__":
    tests = [
        "Should I draft Fernando Mendoza?",
        "Should I draft Chandler Morris or Cade Klubnik?",
        "Which rookie QB is the best value?",
        "Which players on my team should I move before the season?",
        "How can I use my QB depth to upgrade RB?",
    ]

    for t in tests:
        print("\nQUESTION:", t)
        print(understand_question(t))
