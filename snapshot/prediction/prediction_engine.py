
from snapshot.context.feature_reality_engine import FeatureRealityEngine
from snapshot.simulation.weekly_scoring_engine import WeeklyScoringEngine
import numpy as np

class PredictionEngine:

    def __init__(self):
        self.context = FeatureRealityEngine()
        self.sim = WeeklyScoringEngine()

    def predict(self, player, n_sims=500):

        # 1. build context
        enriched = self.context.build(player)

        # 2. run simulation
        sims = []

        for _ in range(n_sims):
            result = self.sim.simulate_player_week(enriched)
            sims.append(result["mean"])

        sims = np.array(sims)

        prediction = {
            "player_name": player.get("player_name", "UNKNOWN"),
            "projected_points": float(np.mean(sims)),
            "floor": float(np.percentile(sims, 10)),
            "ceiling": float(np.percentile(sims, 90)),
            "boom_probability": float(np.mean(sims > np.mean(sims) * 1.5)),
            "confidence": float(1 - np.std(sims) / (np.mean(sims) + 1e-6))
        }

        return prediction
