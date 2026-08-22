from __future__ import annotations

from typing import Any

from gm_assistant.safety.protected_gm_answer import answer_gm_question_protected as answer_gm_question


def route_question(
    question: str,
    owner_team_name: str = "Tommy Bruggeman",
    conversation_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compatibility wrapper.

    Streamlit/UI code historically called route_question().
    The terminal smoke tests call answer_gm_question().
    To prevent the app from using stale routing logic, route all UI calls
    through the same GM brain entrypoint.
    """
    return answer_gm_question(
        question=question,
        owner_team_name=owner_team_name,
        conversation_state=conversation_state,
    )
