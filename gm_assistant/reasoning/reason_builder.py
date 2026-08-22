from __future__ import annotations

from gm_assistant.models.brain_decision import DecisionOption


class ReasonBuilder:
    """
    Converts raw engine output into structured reasoning.
    Engines compute facts.
    ReasonBuilder explains why those facts matter.
    """

    @staticmethod
    def rookie_option(candidate: dict, roster_context: dict | None = None) -> DecisionOption:
        player = candidate.get("player", candidate)

        name = (
            player.get("player_name")
            or player.get("name")
            or candidate.get("player_name")
            or "Unknown Player"
        )

        pos = player.get("pos") or candidate.get("pos") or ""

        reasons = []

        if pos == "RB":
            reasons.append("Running back is one of the biggest pressure points on your roster.")

        elif pos == "WR":
            reasons.append("Adds another premium dynasty asset with long-term flexibility.")

        ev = candidate.get("ev") or candidate.get("expected_value")
        if ev is not None:
            reasons.append(f"Grades as one of the highest expected-value players on the board.")

        marginal = candidate.get("marginal_gain")
        if marginal is not None:
            reasons.append("Provides meaningful value over the next available tier.")

        risks = [
            "Landing spot will influence early production.",
            "Rookie development is never guaranteed."
        ]

        return DecisionOption(
            title=f"{name} ({pos})",
            recommendation=f"This would be one of my preferred options at your draft slot.",
            reasons=reasons,
            risks=risks,
            upside="Could become a long-term cornerstone if development goes as expected.",
            downside="May need time before reaching full fantasy value.",
            confidence=0.80,
        )
