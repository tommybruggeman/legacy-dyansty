from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from snapshot.projections.historical_stat_projection_engine import HistoricalStatProjectionEngine
from snapshot.projections.player_projection_engine import PlayerProjectionEngine
from snapshot.opportunity.opportunity_engine import OpportunityEngine


@dataclass
class BlendedProjection:
    player_name: str
    pos: str
    projected_stats: Dict
    confidence: str
    blend_note: str
    historical_note: str
    opportunity_role: str
    opportunity_score: float


class ProjectionBlendEngine:
    """
    Blends historical baseline + opportunity + market/asset signal.

    V1:
    - Historical projection is the anchor
    - Opportunity adjusts volume-sensitive stats
    - Asset/win-now projection acts as fallback/market pressure
    """

    def __init__(self):
        self.historical_engine = HistoricalStatProjectionEngine()
        self.market_engine = PlayerProjectionEngine()
        self.opportunity_engine = OpportunityEngine()

    def project(self, player: Dict) -> BlendedProjection:
        historical = self.historical_engine.project(player)
        market = self.market_engine.project(player)
        opportunity = self.opportunity_engine.evaluate(player)

        hist_stats = dict(historical.projected_stats)
        market_stats = dict(market.projected_stats)

        opp_multiplier = self._opportunity_multiplier(opportunity.opportunity_score)
        asset_multiplier = self._asset_multiplier(player)

        blended = self._blend_stats(
            pos=player.get("pos", historical.pos),
            historical=hist_stats,
            market=market_stats,
            opp_multiplier=opp_multiplier,
            asset_multiplier=asset_multiplier,
        )

        confidence = self._blend_confidence(historical.confidence, opportunity.confidence)

        return BlendedProjection(
            player_name=player.get("player_name", "Unknown"),
            pos=player.get("pos", historical.pos),
            projected_stats=blended,
            confidence=confidence,
            blend_note=(
                f"Historical anchor adjusted by opportunity "
                f"({opportunity.opportunity_score}, {opportunity.role}) "
                f"and asset/win-now signal."
            ),
            historical_note=historical.projection_note,
            opportunity_role=opportunity.role,
            opportunity_score=opportunity.opportunity_score,
        )

    def _opportunity_multiplier(self, score: float) -> float:
        """
        Converts opportunity score into a conservative projection adjustment.

        50 = neutral
        90 = about +8%
        30 = about -4%
        """
        return 1 + ((score - 50) / 500)

    def _asset_multiplier(self, player: Dict) -> float:
        asset = float(player.get("dynasty_asset_score", 50) or 50)
        win_now = float(player.get("win_now_score", asset) or asset)
        signal = (asset * 0.35) + (win_now * 0.65)

        return 1 + ((signal - 50) / 1000)

    def _blend_value(
        self,
        stat: str,
        historical: Dict,
        market: Dict,
        hist_weight: float,
        market_weight: float,
        multiplier: float,
    ) -> float:
        h = float(historical.get(stat, 0) or 0)
        m = float(market.get(stat, h) or h)

        value = (h * hist_weight) + (m * market_weight)
        value *= multiplier

        return max(0, value)

    def _blend_stats(
        self,
        pos: str,
        historical: Dict,
        market: Dict,
        opp_multiplier: float,
        asset_multiplier: float,
    ) -> Dict:
        multiplier = opp_multiplier * asset_multiplier

        base = {
            "player_name": historical.get("player_name", market.get("player_name", "Unknown")),
            "pos": pos,
        }

        if pos == "QB":
            base.update({
                "pass_yards": round(self._blend_value("pass_yards", historical, market, 0.80, 0.20, multiplier), 1),
                "pass_tds": round(self._blend_value("pass_tds", historical, market, 0.75, 0.25, multiplier), 2),
                "interceptions": round(self._blend_value("interceptions", historical, market, 0.90, 0.10, 1.0), 2),
                "rush_yards": round(self._blend_value("rush_yards", historical, market, 0.80, 0.20, multiplier), 1),
                "rush_tds": round(self._blend_value("rush_tds", historical, market, 0.70, 0.30, multiplier), 2),
            })
            return base

        base.update({
            "rush_yards": round(self._blend_value("rush_yards", historical, market, 0.80, 0.20, multiplier), 1),
            "rush_tds": round(self._blend_value("rush_tds", historical, market, 0.75, 0.25, multiplier), 2),
            "receptions": round(self._blend_value("receptions", historical, market, 0.80, 0.20, multiplier), 1),
            "rec_yards": round(self._blend_value("rec_yards", historical, market, 0.80, 0.20, multiplier), 1),
            "rec_tds": round(self._blend_value("rec_tds", historical, market, 0.75, 0.25, multiplier), 2),
            "fumbles": round(float(historical.get("fumbles", market.get("fumbles", 0.03)) or 0.03), 2),
        })

        return base

    def _blend_confidence(self, historical_confidence: str, opportunity_confidence: str) -> str:
        if historical_confidence == "HIGH" and opportunity_confidence == "HIGH":
            return "HIGH"
        if historical_confidence in ["HIGH", "MEDIUM"] and opportunity_confidence in ["HIGH", "MEDIUM"]:
            return "MEDIUM"
        return "LOW"
