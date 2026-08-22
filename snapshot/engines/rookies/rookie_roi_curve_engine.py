from __future__ import annotations

from collections import defaultdict
import math


class RookieROICurveEngine:
    """
    Learns VALUE CURVES over time, not flat averages.

    This is the real dynasty model:
    - Year 1 value
    - Year 2 value
    - Year 3 value
    - confidence weighting
    """

    def __init__(self):
        # (pos, round_bucket) -> year -> values
        self.curves = defaultdict(lambda: defaultdict(float))
        self.counts = defaultdict(lambda: defaultdict(int))

    # -----------------------------
    # ARCHETYPE
    # -----------------------------
    def archetype(self, pos: str) -> str:
        pos = (pos or "").upper()
        return pos if pos in {"QB", "RB", "WR", "TE"} else "UNK"

    # -----------------------------
    # DRAFT BUCKET
    # -----------------------------
    def bucket(self, rookie_rank: int) -> str:
        if rookie_rank is None:
            return "DEPTH"
        if rookie_rank <= 3:
            return "ROUND_1"
        if rookie_rank <= 6:
            return "ROUND_2"
        if rookie_rank <= 10:
            return "ROUND_3"
        return "DEPTH"

    # -----------------------------
    # YEAR MAPPING (CRITICAL FIX)
    # -----------------------------
    def get_year(self, rookie_rank: int) -> str:
        """
        Proxy for career timeline effect.
        (We refine later with real season tracking)
        """
        if rookie_rank <= 3:
            return "YEAR_1_ELITE"
        if rookie_rank <= 6:
            return "YEAR_1_CORE"
        return "YEAR_1_DEPTH"

    # -----------------------------
    # INGEST DATA
    # -----------------------------
    def ingest(self, rows: list[dict]):
        for r in rows:
            pos = self.archetype(r.get("pos"))
            bucket = self.bucket(r.get("rookie_rank"))
            year = self.get_year(r.get("rookie_rank"))

            value = float(r.get("season_ppg") or r.get("outcome_score") or 0)

            key = (pos, bucket)

            self.curves[key][year] += value
            self.counts[key][year] += 1

    # -----------------------------
    # NORMALIZED CURVES
    # -----------------------------
    def compute_curves(self):
        output = {}

        for key, years in self.curves.items():
            pos, bucket = key
            output[key] = {}

            for year, total in years.items():
                count = self.counts[key][year]
                avg = total / count if count else 0

                output[key][year] = round(avg, 3)

        return output

    # -----------------------------
    # CONFIDENCE WEIGHTING
    # -----------------------------
    def confidence(self, count: int) -> float:
        """
        Smooth confidence so small samples don't dominate.
        """
        return round(1 - math.exp(-count / 5), 3)

    # -----------------------------
    # STRATEGY OUTPUT
    # -----------------------------
    def strategy(self):
        curves = self.compute_curves()

        summary = {}

        for (pos, bucket), years in curves.items():

            counts = sum(self.counts[(pos, bucket)].values())
            conf = self.confidence(counts)

            year1 = years.get("YEAR_1_ELITE", 0) + years.get("YEAR_1_CORE", 0) + years.get("YEAR_1_DEPTH", 0)

            # long-term projection (simplified but powerful)
            year2 = year1 * 0.75
            year3 = year1 * 0.55

            score = (
                0.5 * year1 +
                0.3 * year2 +
                0.2 * year3
            ) * conf

            summary[f"{pos}_{bucket}"] = {
                "year1": round(year1, 3),
                "year2": round(year2, 3),
                "year3": round(year3, 3),
                "confidence": conf,
                "roi_score": round(score, 3),
            }

        return summary
