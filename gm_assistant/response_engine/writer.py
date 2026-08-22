from __future__ import annotations

from gm_assistant.response_engine.models import ResponsePlan


BANNED_GENERIC_OPENERS = [
    "My GM read:",
    "I would not treat this like",
    "My 3-step plan:",
    "Your current build is",
    "Roster direction:",
]


def write_coach_response(plan: ResponsePlan) -> str:
    paragraphs: list[str] = []

    paragraphs.append(plan.opening.strip())

    for point in plan.body_points:
        if point and point.strip():
            paragraphs.append(point.strip())

    if plan.caveat:
        paragraphs.append(plan.caveat.strip())

    if plan.next_action:
        paragraphs.append(plan.next_action.strip())

    text = "\n\n".join(paragraphs[: plan.max_paragraphs])

    for banned in BANNED_GENERIC_OPENERS:
        text = text.replace(banned, "")

    return text.strip()
