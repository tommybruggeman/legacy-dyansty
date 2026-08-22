from __future__ import annotations


class EvidenceAggregator:
    """
    Sorts, filters and prioritizes evidence.
    """

    def aggregate(self, evidence):

        evidence = sorted(
            evidence,
            key=lambda e: e.importance,
            reverse=True,
        )

        # TODO:
        # dedupe
        # category balancing
        # confidence weighting
        # stale evidence filtering

        return evidence[:10]
