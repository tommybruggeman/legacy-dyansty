from __future__ import annotations

from gm_assistant.pipeline.models import ReasoningResult


def write_gm_response(result: ReasoningResult) -> str:
    lines = []

    if result.decision == "TRADE_RETURN_VALUE":
        player = result.evidence.get("player", "that player")
        lines.append(f"For **{player}**, I would set the price before shopping.")
        lines.append("")
        lines.append("My ask:")
        for i, action in enumerate(result.actions, start=1):
            lines.append(f"{i}. {action}")
        lines.append("")

        if result.reasons:
            lines.append("Why:")
            for reason in result.reasons:
                lines.append(f"- {reason}")
            lines.append("")

        if result.risks:
            lines.append("Risks:")
            for risk in result.risks:
                lines.append(f"- {risk}")
            lines.append("")

        lines.append("My lean: do not move the player just to escape discomfort. Move only if the return improves weekly lineup strength, cap flexibility, or future value.")
        return "\n".join(lines)

    return "\n".join(result.reasons or [result.recommendation])
