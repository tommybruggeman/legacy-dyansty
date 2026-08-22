from __future__ import annotations

from typing import Any, Dict, Optional

from gm_assistant.gm_brain import answer_gm_question
from gm_assistant.engines.rookie_decision_engine import (
    answer_rookie_question,
    is_rookie_question,
)
from gm_assistant.safety.brain_context import build_brain_context


def answer_gm_question_protected(
    question: str,
    owner_name: str,
    conversation_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    brain_context = build_brain_context(
        question=question,
        owner_name=owner_name,
        conversation_state=conversation_state,
    ).as_dict()

    # Phase 1 plugin: rookie decision engine.
    if is_rookie_question(question):
        rookie_answer = answer_rookie_question(
            question,
            owner_name,
            conversation_state=conversation_state,
            brain_context=brain_context,
        )

        summary = str(rookie_answer.get("summary") or "").lower()
        if summary and "specific question first" not in summary:
            rookie_answer["brain_context_used"] = True
            rookie_answer["failsafe_used"] = False
            return rookie_answer

    # Proven brain fallback.
    fallback = answer_gm_question(
        question,
        owner_name,
        conversation_state=conversation_state,
    )

    if isinstance(fallback, dict):
        fallback["failsafe_used"] = True
        fallback["brain_context_used"] = False
        return fallback

    return {
        "answer_type": "failsafe_error",
        "intent": "unknown",
        "decision": "FALLBACK_FAILED",
        "summary": "I could not safely answer this GM question.",
        "conversation_state": conversation_state or {},
        "failsafe_used": True,
    }
