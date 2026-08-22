from __future__ import annotations

from gm_assistant.models.evidence import Evidence


class EvidenceEngine:

    def rookie_evidence(
        self,
        candidate: dict,
        roster_context: dict | None = None,
    ) -> list[Evidence]:

        evidence = []

        pos = (
            candidate.get("player", {})
            .get("pos")
            or candidate.get("pos")
        )

        if pos == "RB":
            evidence.append(
                Evidence(
                    category="Roster Fit",
                    importance=0.95,
                    statement="Running back is currently one of your biggest roster needs.",
                    source="roster_strength_context",
                )
            )

        ev = candidate.get("ev")

        if ev is not None:
            evidence.append(
                Evidence(
                    category="Expected Value",
                    importance=.90,
                    statement="Projects as one of the highest-value players remaining.",
                    source="rookie_cross_elastic_engine",
                    value=ev,
                )
            )

        marginal = candidate.get("marginal_gain")

        if marginal is not None:
            evidence.append(
                Evidence(
                    category="Tier Break",
                    importance=.82,
                    statement="Provides meaningful value over the next tier of prospects.",
                    source="rookie_cross_elastic_engine",
                    value=marginal,
                )
            )

        evidence.sort(
            key=lambda e: e.importance,
            reverse=True,
        )

        return evidence
