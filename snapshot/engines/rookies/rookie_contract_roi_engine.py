from __future__ import annotations

from collections import defaultdict


class RookieContractROIEngine:
    """
    Converts rookie production + rookie contract scale into
    $ efficiency and draft strategy signals.

    This is NOT player evaluation.
    This is market efficiency modeling.
    """

    def __init__(self, rookie_salary_scale: dict = None):
        """
        rookie_salary_scale comes from your settings layer.

        Example:
        {
            "ROUND_1": 15,
            "ROUND_2": 10,
            "ROUND_3": 5,
            "DEPTH": 1
        }
        """
        self.scale = rookie_salary_scale or {
            "ROUND_1": 15,
            "ROUND_2": 10,
            "ROUND_3": 5,
            "DEPTH": 1
        }

        self.production = defaultdict(float)
        self.counts = defaultdict(int)

    # -----------------------------
    # ARCHETYPE CLASSIFICATION
    # -----------------------------
    def archetype(self, pos: str) -> str:
        pos = (pos or "").upper()

        if pos == "RB":
            return "RB"
        if pos == "WR":
            return "WR"
        if pos == "QB":
            return "QB"
        if pos == "TE":
            return "TE"

        return "UNK"

    # -----------------------------
    # CONTRACT BUCKETING
    # -----------------------------
    def contract_bucket(self, rookie_rank: int) -> str:
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
    # INGEST HISTORICAL DATA
    # -----------------------------
    def ingest(self, rows: list[dict]):
        for r in rows:
            pos = self.archetype(r.get("pos"))
            bucket = self.contract_bucket(r.get("rookie_rank"))

            value = float(r.get("season_ppg") or r.get("outcome_score") or 0)

            key = (pos, bucket)

            self.production[key] += value
            self.counts[key] += 1

    # -----------------------------
    # AVERAGE PRODUCTION
    # -----------------------------
    def avg_production(self):
        out = {}

        for k, total in self.production.items():
            count = self.counts[k]
            out[k] = round(total / count, 3) if count else 0.0

        return out

    # -----------------------------
    # CONTRACT EFFICIENCY MODEL
    # -----------------------------
    def contract_efficiency(self):
        """
        Computes $ per fantasy point proxy.
        """

        prod = self.avg_production()
        efficiency = {}

        for (pos, bucket), value in prod.items():
            cost = self.scale.get(bucket, 1)

            if value == 0:
                eff = 0
            else:
                eff = value / cost

            efficiency[(pos, bucket)] = round(eff, 4)

        return efficiency

    # -----------------------------
    # DRAFT STRATEGY OUTPUT
    # -----------------------------
    def strategy(self):
        eff = self.contract_efficiency()

        strategy = defaultdict(float)

        for (pos, bucket), value in eff.items():
            strategy[pos] += value

        return {k: round(v, 4) for k, v in strategy.items()}
