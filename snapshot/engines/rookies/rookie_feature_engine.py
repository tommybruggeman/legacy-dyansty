from __future__ import annotations
from snapshot.engines.rookies.rookie_feedback_calibration_engine import RookieFeedbackCalibrationEngine
from snapshot.engines.rookies.rookie_landing_environment_engine import RookieLandingEnvironmentEngine
from snapshot.engines.rookies.rookie_archetype_engine import RookieArchetypeEngine
from dataclasses import dataclass
def clamp(x: float, lo=0.0, hi=1.0) -> float:
    return max(lo, min(hi, x))
@dataclass
class RookieFeatures:
    qb_start_prob: float = 0.0
    rushing_upside: float = 0.0
    workload_prob: float = 0.0
    pass_catching: float = 0.0
    target_share_prob: float = 0.0
    separation: float = 0.0
    route_share: float = 0.0
    red_zone_role: float = 0.0
    age_curve: float = 0.5
    landing_environment: float = 0.5
    draft_signal: float = 0.5
class RookieFeatureEngine:
    def __init__(self):
        self.calibrator = RookieFeedbackCalibrationEngine()
        self.landing_engine = RookieLandingEnvironmentEngine()
        self.archetype_engine = RookieArchetypeEngine()
    """
    Converts raw draft board signals into structured football probabilities.
    This is NOT ranking logic — this is reality modeling.
    """
    # ---------------------------
    # QB
    # ---------------------------
    def qb_features(self, d: dict) -> RookieFeatures:
        draft_capital = d.get("draft_signal", 0)
        qb_start_prob = clamp((draft_capital - 40) / 60)
        rushing_upside = clamp(d.get("rushing_profile", 0.3))
        return RookieFeatures(
            qb_start_prob=qb_start_prob,
            rushing_upside=rushing_upside,
            draft_signal=draft_capital / 100,
        )
    # ---------------------------
    # RB
    # ---------------------------
    def rb_features(self, d: dict) -> RookieFeatures:
        draft_capital = d.get("draft_signal", 0)
        workload_prob = clamp((draft_capital - 35) / 65)
        pass_catching = clamp(d.get("pass_game_role", 0.4))
        return RookieFeatures(
            workload_prob=workload_prob,
            pass_catching=pass_catching,
            draft_signal=draft_capital / 100,
        )
    # ---------------------------
    # WR
    # ---------------------------
    def wr_features(self, d: dict) -> RookieFeatures:
        draft_capital = d.get("draft_signal", 0)
        target_share_prob = clamp((draft_capital - 30) / 70)
        separation = clamp(d.get("athletic_profile", 0.5))
        return RookieFeatures(
            target_share_prob=target_share_prob,
            separation=separation,
            draft_signal=draft_capital / 100,
        )
    # ---------------------------
    # TE
    # ---------------------------
    def te_features(self, d: dict) -> RookieFeatures:
        draft_capital = d.get("draft_signal", 0)
        route_share = clamp((draft_capital - 35) / 65)
        red_zone_role = clamp(d.get("size_profile", 0.5))
        return RookieFeatures(
            route_share=route_share,
            red_zone_role=red_zone_role,
            draft_signal=draft_capital / 100,
        )
    # ---------------------------
    # MAIN ROUTER
    # ---------------------------
    def build(self, pos: str, d: dict, feedback_weights: dict = None) -> RookieFeatures:
        archetype = self.archetype_engine.build(pos, d)
        weights = feedback_weights or self.calibrator.weights
        archetype = self.archetype_engine.build(pos, d)
        pos = (pos or "").upper()
        if pos == "QB":
            return self.qb_features(d)
        if pos == "RB":
            return self.rb_features(d)
        if pos == "WR":
            return self.wr_features(d)
        if pos == "TE":
            return self.te_features(d)
        return RookieFeatures()