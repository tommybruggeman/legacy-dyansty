from __future__ import annotations

import random
import numpy as np


class MonteCarloEngine:
    """
    First real intelligence layer.

    NOT rules.
    NOT heuristics.

    Pure probabilistic trajectory simulation.
    """

    def simulate_player(self, player_vector, n_sims: int = 1000):
        """
        Simulates value curve outcomes over time.
        """

        results = []

        age = player_vector.get("age", 26)
        volatility = player_vector.get("volatility", 0.2)
        base_value = player_vector.get("market_score", 50)

        for _ in range(n_sims):

            yearly_values = []
            current = base_value

            for year in range(1, 6):

                # stochastic drift (core learning mechanism starter)
                noise = np.random.normal(0, volatility * 10)

                # mild natural aging decay (NOT rule-based decisioning)
                decay = (age + year - 26) * 0.8

                value = current - decay + noise

                yearly_values.append(value)

                current = value

            results.append(yearly_values)

        return np.array(results)

    def summarize(self, sims):
        """
        Converts raw simulations into decision-ready distributions.
        """

        sims = np.array(sims)

        return {
            "year_1_mean": float(np.mean(sims[:, 0])),
            "year_3_mean": float(np.mean(sims[:, 2])),
            "year_5_mean": float(np.mean(sims[:, 4])),

            "volatility": float(np.std(sims)),
            "downside_risk": float(np.percentile(sims[:, 4], 10)),
            "upside": float(np.percentile(sims[:, 4], 90)),
        }
