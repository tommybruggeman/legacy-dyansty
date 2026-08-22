from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from gm_assistant.safety.brain_context import build_brain_context


def _is_valid_answer(answer: Any) -> bool:
    if not isinstance(answer, dict):
        return False

    summary = str(answer.get("summary") or "").strip()
    decision = str(answer.get("decision") or "").strip()

    if not summary:
        return False

    # Avoid accepting placeholder generic answers from unfinished routes.
    bad_phrases = [
        "i’d answer the specific question first",
        "i'd answer the specific question first",
        "generic answer",
        "todo",
        "not implemented",
    ]

    lowered = summary.lower()
    if any(p in lowered for p in bad_phrases):
        return False

    return bool(decision)


def protected_answer(
    *,
    question: str,
    owner_name: str,
    primary_answer_fn: Callable[..., Dict[str, Any]],
    fallback_answer_fn: Callable[..., Dict[str, Any]],
    conversation_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Future-proof wrapper.

    Any new architecture can be tried as the primary engine.
    If it fails, returns placeholder text, or loses intelligence,
    this falls back to the current proven GM brain.
    """

    brain_context = build_brain_context(
        question=question,
        owner_name=owner_name,
        conversation_state=conversation_state,
    )

    try:
        primary = primary_answer_fn(
            question,
            owner_name,
            conversation_state=conversation_state,
            brain_context=brain_context.as_dict(),
        )

        if _is_valid_answer(primary):
            primary.setdefault("brain_context_used", True)
            primary.setdefault("failsafe_used", False)
            return primary

    except TypeError:
        # Allows older functions that do not accept brain_context yet.
        try:
            primary = primary_answer_fn(
                question,
                owner_name,
                conversation_state=conversation_state,
            )
            if _is_valid_answer(primary):
                primary.setdefault("brain_context_used", False)
                primary.setdefault("failsafe_used", False)
                return primary
        except Exception:
            pass

    except Exception:
        pass

    fallback = fallback_answer_fn(
        question,
        owner_name,
        conversation_state=conversation_state,
    )

    if isinstance(fallback, dict):
        fallback.setdefault("failsafe_used", True)
        fallback.setdefault("brain_context_used", False)
        return fallback

    return {
        "answer_type": "failsafe_error",
        "intent": "unknown",
        "decision": "FALLBACK_FAILED",
        "summary": "I could not produce a safe GM answer from either the new engine or the existing GM brain.",
        "conversation_state": conversation_state or {},
        "failsafe_used": True,
    }
