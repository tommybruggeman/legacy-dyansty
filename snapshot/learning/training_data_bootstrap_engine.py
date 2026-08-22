
import numpy as np
import pandas as pd

class TrainingDataBootstrapEngine:

    def generate(self, n_players=200, weeks=5):

        rows = []

        for week in range(weeks):

            for i in range(n_players):

                context_score = np.random.normal(15, 5)
                volatility = np.random.uniform(0.1, 0.5)

                predicted = context_score + np.random.normal(0, 2)
                actual = context_score + np.random.normal(0, volatility * 10)

                rows.append({
                    "week": week,
                    "player_id": i,
                    "context_score": context_score,
                    "volatility": volatility,
                    "predicted_mean": predicted,
                    "actual_points": actual,
                    "error": actual - predicted,
                    "abs_error": abs(actual - predicted)
                })

        return pd.DataFrame(rows)
