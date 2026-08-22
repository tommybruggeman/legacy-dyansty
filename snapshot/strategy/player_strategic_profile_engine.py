from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from snapshot.projections.player_forecast_engine import PlayerForecastEngine
from snapshot.opportunity.opportunity_engine import OpportunityEngine
from snapshot.risk.player_volatility_engine import PlayerVolatilityEngine


@dataclass
class PlayerStrategicProfile:
    player_name: str
    pos: str
    strategic_label: str
    action: str
    confidence: str
    median_projection: float
    opportunity_score: float
    volatility_label: str
    contract_flag: str
    asset_score: float
    win_now_score: float
    explanation: str


class PlayerStrategicProfileEngine:
    """
    Converts player analytics into GM strategy.

    Combines:
    - Forecast
    - Opportunity
    - Volatility
    - Contract/salary
    - Dynasty asset value
    - Win-now value
    """

    def __init__(self, samples: int = 500):
        self.forecast_engine = PlayerForecastEngine(samples=samples)
        self.opportunity_engine = OpportunityEngine()
        self.volatility_engine = PlayerVolatilityEngine()

    def evaluate(self, player: Dict) -> PlayerStrategicProfile:
        forecast = self.forecast_engine.forecast(player)
        opportunity = self.opportunity_engine.evaluate(player)
        volatility = self.volatility_engine.evaluate(player)

        asset = float(player.get("dynasty_asset_score", 50) or 50)
        win_now = float(player.get("win_now_score", asset) or asset)
        salary = float(player.get("salary", 0) or 0)
        years = float(player.get("years", 1) or 1)

        contract_flag = self._contract_flag(salary, years, asset, win_now)
        label, action = self._classify(
            forecast_median=forecast.median,
            opportunity_score=opportunity.opportunity_score,
            volatility_label=volatility.boom_bust_label,
            asset=asset,
            win_now=win_now,
            contract_flag=contract_flag,
        )

        confidence = self._confidence(
            forecast.confidence,
            opportunity.confidence,
            volatility.confidence,
        )

        explanation = self._explain(
            player=player,
            label=label,
            action=action,
            forecast=forecast,
            opportunity=opportunity,
            volatility=volatility,
            contract_flag=contract_flag,
            salary=salary,
            years=years,
            asset=asset,
            win_now=win_now,
        )

        return PlayerStrategicProfile(
            player_name=player.get("player_name", "Unknown"),
            pos=player.get("pos", "RB"),
            strategic_label=label,
            action=action,
            confidence=confidence,
            median_projection=forecast.median,
            opportunity_score=opportunity.opportunity_score,
            volatility_label=volatility.boom_bust_label,
            contract_flag=contract_flag,
            asset_score=asset,
            win_now_score=win_now,
            explanation=explanation,
        )

    def _contract_flag(self, salary: float, years: float, asset: float, win_now: float) -> str:
        value_signal = (asset * 0.45) + (win_now * 0.55)

        if salary <= 0:
            return "NO_CONTRACT_DATA"

        if salary >= 35 and value_signal < 70:
            return "BAD_CONTRACT"

        if salary >= 25 and value_signal < 60:
            return "OVERPAID"

        if salary <= 8 and value_signal >= 65:
            return "VALUE_CONTRACT"

        if years >= 3 and asset < 50 and win_now < 55:
            return "LONG_TERM_RISK"

        return "FAIR_CONTRACT"

    def _classify(
        self,
        forecast_median: float,
        opportunity_score: float,
        volatility_label: str,
        asset: float,
        win_now: float,
        contract_flag: str,
    ):
        if contract_flag in ["BAD_CONTRACT", "OVERPAID"] and win_now < 65:
            return "CONTRACT_PROBLEM", "SHOP / RESTRUCTURE / CONSIDER EXIT"

        if asset >= 85 and win_now >= 80 and opportunity_score >= 75:
            return "CORE_BUILDING_BLOCK", "HOLD UNLESS MASSIVE OVERPAY"

        if win_now >= 80 and opportunity_score >= 70 and volatility_label in ["VOLATILE_STARTER", "BOOM_BUST"]:
            return "WIN_NOW_HAMMER_WITH_RISK", "START / HOLD, BUT INSURE DEPTH"

        if win_now >= 75 and opportunity_score >= 65:
            return "WIN_NOW_STARTER", "HOLD FOR CONTENDING WINDOW"

        if asset >= 75 and win_now < 65:
            return "DYNASTY_APPRECIATION_ASSET", "HOLD OR BUY IF PRICE IS FAIR"

        if contract_flag == "VALUE_CONTRACT" and opportunity_score >= 55:
            return "VALUE_HOLD", "HOLD AS EFFICIENT DEPTH / FLEX"

        if opportunity_score < 40 and asset < 55:
            return "REPLACEABLE_ASSET", "SHOP / CHURN / REPLACE"

        if forecast_median < 8 and asset < 55 and win_now < 55:
            return "LOW_IMPACT_DEPTH", "CHURN IF BETTER UPSIDE EXISTS"

        return "MONITOR", "HOLD, MONITOR ROLE AND MARKET"

    def _confidence(self, forecast_conf: str, opp_conf: str, vol_conf: str) -> str:
        scores = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}

        avg = (
            scores.get(forecast_conf, 1)
            + scores.get(opp_conf, 1)
            + scores.get(vol_conf, 1)
        ) / 3

        if avg >= 2.7:
            return "HIGH"
        if avg >= 2:
            return "MEDIUM"
        return "LOW"

    def _explain(
        self,
        player: Dict,
        label: str,
        action: str,
        forecast,
        opportunity,
        volatility,
        contract_flag: str,
        salary: float,
        years: float,
        asset: float,
        win_now: float,
    ) -> str:
        return (
            f"{player.get('player_name', 'This player')} profiles as {label}. "
            f"Recommended action: {action}. "
            f"Median projection is {forecast.median}, opportunity is "
            f"{opportunity.opportunity_score} ({opportunity.role}), and volatility profile is "
            f"{volatility.boom_bust_label}. Contract flag is {contract_flag} "
            f"at ${salary}/{years} years. Asset score {asset}, win-now score {win_now}."
        )
