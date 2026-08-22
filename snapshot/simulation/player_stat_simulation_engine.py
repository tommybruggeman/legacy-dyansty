from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List

from snapshot.scoring.scoring_engine import ScoringEngine


@dataclass
class SimulationResult:
    player_name: str
    position: str
    median: float
    low: float
    high: float
    ceiling: float
    floor: float
    samples: int


class PlayerStatSimulationEngine:
    """
    Turns projected player stat baselines into fantasy point outcome ranges.
    """

    def __init__(self, samples: int = 1000):
        self.samples = samples
        self.scoring = ScoringEngine()

    def simulate(self, player: Dict) -> SimulationResult:
        outcomes: List[float] = []

        for _ in range(self.samples):
            simulated_player = self._simulate_stat_line(player)
            score = self.scoring.score(simulated_player)
            outcomes.append(score)

        outcomes.sort()

        return SimulationResult(
            player_name=player.get("player_name", "Unknown"),
            position=player.get("pos", "RB"),
            floor=round(self._percentile(outcomes, 10), 2),
            low=round(self._percentile(outcomes, 25), 2),
            median=round(self._percentile(outcomes, 50), 2),
            high=round(self._percentile(outcomes, 75), 2),
            ceiling=round(self._percentile(outcomes, 90), 2),
            samples=self.samples,
        )

    def _simulate_stat_line(self, player: Dict) -> Dict:
        simulated = dict(player)

        pos = simulated.get("pos", "RB")

        if pos == "QB":
            variance = {
                "pass_yards": 0.18,
                "pass_tds": 0.35,
                "interceptions": 0.50,
                "rush_yards": 0.30,
                "rush_tds": 0.60,
            }
        else:
            variance = {
                "rush_yards": 0.30,
                "rush_tds": 0.55,
                "receptions": 0.25,
                "rec_yards": 0.30,
                "rec_tds": 0.55,
                "fumbles": 0.50,
            }

        for stat, vol in variance.items():
            base = float(simulated.get(stat, 0) or 0)
            simulated[stat] = max(0, random.gauss(base, max(base * vol, 0.25)))

        return simulated

    def _percentile(self, values: List[float], percentile: int) -> float:
        if not values:
            return 0.0

        index = int((percentile / 100) * (len(values) - 1))
        return values[index]
