from __future__ import annotations

from gm_assistant.reasoning.models import BrainDecision


def write_decision(decision: BrainDecision) -> str:
    parts: list[str] = [decision.thesis.strip()]

    for reason in decision.reasons:
        if reason:
            parts.append(reason.strip())

    for caveat in decision.caveats[:1]:
        if caveat:
            parts.append(caveat.strip())

    if decision.next_action:
        parts.append(decision.next_action.strip())

    return "\n\n".join(parts[:6]).strip()
