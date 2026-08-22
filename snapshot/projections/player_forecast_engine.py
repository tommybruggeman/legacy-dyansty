from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from snapshot.projections.projection_blend_engine import ProjectionBlendEngine
from snapshot.simulation.player_stat_simulation_engine import PlayerStatSimulationEngine
from snapshot.opportunity.opportunity_engine import OpportunityEngine


@dataclass
class PlayerForecast:
    player_name: str
    pos: str
    projected_stats: Dict
    median: float
    floor: float
    low: float
    high: float
    ceiling: float
    opportunity_score: float
    opportunity_role: str
    confidence: str
    explanation: str


class PlayerForecastEngine:
    """
    One-stop forecasting engine for the GM Assistant.

    Combines:
    - Blended stat projection
    - Opportunity profile
    - Monte Carlo simulation
    - Human-readable explanation
    """

    def __init__(self, samples: int = 1000):
        self.blend_engine = ProjectionBlendEngine()
        self.simulation_engine = PlayerStatSimulationEngine(samples=samples)
        self.opportunity_engine = OpportunityEngine()

    def forecast(self, player: Dict) -> PlayerForecast:
        blended = self.blend_engine.project(player)
        sim = self.simulation_engine.simulate(blended.projected_stats)
        opportunity = self.opportunity_engine.evaluate(player)

        explanation = self._explain(blended, sim, opportunity)

        return PlayerForecast(
            player_name=blended.player_name,
            pos=blended.pos,
            projected_stats=blended.projected_stats,
            median=sim.median,
            floor=sim.floor,
            low=sim.low,
            high=sim.high,
            ceiling=sim.ceiling,
            opportunity_score=opportunity.opportunity_score,
            opportunity_role=opportunity.role,
            confidence=blended.confidence,
            explanation=explanation,
        )

    def _explain(self, blended, sim, opportunity) -> str:
        return (
            f"{blended.player_name} projects for a median outcome of {sim.median} fantasy points, "
            f"with a realistic floor/ceiling range of {sim.floor} to {sim.ceiling}. "
            f"The projection is anchored by historical production and adjusted for "
            f"{opportunity.role.lower()} opportunity ({opportunity.opportunity_score}). "
            f"Confidence is {blended.confidence}."
        )
