from __future__ import annotations

import math

from snapshot.engines.rookies.rookie_feature_engine import RookieFeatureEngine


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


class RookieFEVEngine:
    def __init__(self):
        self.feature_engine = RookieFeatureEngine()
    """
    Converts rookie profile signals into Expected Fantasy Value (FEV).
    This is NOT rankings. This is probability-weighted fantasy production.
    """

    def qb_value(self, qb_start_prob: float, rushing_upside: float) -> float:
        return (
            0.75 * qb_start_prob +
            0.25 * rushing_upside
        )

    def rb_value(self, workload_prob: float, pass_catching: float) -> float:
        return (
            0.70 * workload_prob +
            0.30 * pass_catching
        )

    def wr_value(self, target_share_prob: float, separation: float) -> float:
        return (
            0.65 * target_share_prob +
            0.35 * separation
        )

    def te_value(self, route_share: float, red_zone_role: float) -> float:
        return (
            0.60 * route_share +
            0.40 * red_zone_role
        )

    def archetype_score(self, pos: str, features: dict) -> float:
        pos = (pos or "").upper()

        if pos == "QB":
            return self.qb_value(
                features.get("qb_start_prob", 0),
                features.get("rushing_upside", 0),
            )

        if pos == "RB":
            return self.rb_value(
                features.get("workload_prob", 0),
                features.get("pass_catching", 0),
            )

        if pos == "WR":
            return self.wr_value(
                features.get("target_share_prob", 0),
                features.get("separation", 0),
            )

        if pos == "TE":
            return self.te_value(
                features.get("route_share", 0),
                features.get("red_zone_role", 0),
            )

        return 0.0

    def fev(self, pos: str, features: dict) -> float:
        """
        Final Expected Fantasy Value (0–100 scale)
        """
        archetype = self.archetype_score(pos, features)

        age_curve = features.get("age_curve", 0)
        landing = features.get("landing_environment", 0)
        draft_signal = features.get("draft_signal", 0)

        fev_raw = (
            0.45 * archetype +
            0.25 * draft_signal +
            0.20 * age_curve +
            0.10 * landing
        )

        return round(sigmoid(fev_raw) * 100, 2)
