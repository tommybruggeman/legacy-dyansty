
import numpy as np


class ChampionshipEngine:

    def __init__(self):
        pass

    # ----------------------------------------
    # Convert team value → win probability
    # ----------------------------------------
    def _value_to_win_prob(self, team_value):
        # soft sigmoid mapping (NOT rules, just transformation curve)
        return 1 / (1 + np.exp(-(team_value - 100) / 25))

    # ----------------------------------------
    # CHAMPIONSHIP SIMULATION
    # ----------------------------------------
    def simulate_championship_curve(self, team_curve):
        """
        team_curve = [
            {year, team_value, risk},
            ...
        ]
        """

        results = []

        for y in team_curve:

            win_prob = self._value_to_win_prob(y["team_value"])

            # championship = win * playoff funnel (simplified probabilistic reduction)
            playoff_prob = win_prob * 0.6
            championship_prob = playoff_prob * 0.25

            results.append({
                "year": y["year"],
                "win_probability": float(win_prob),
                "playoff_probability": float(playoff_prob),
                "championship_probability": float(championship_prob),
            })

        return {
            "curve": results,
            "best_year": max(results, key=lambda x: x["championship_probability"]),
            "avg_championship_odds": float(np.mean([r["championship_probability"] for r in results]))
        }

    # ----------------------------------------
    # TRADE IMPACT ON TITLES
    # ----------------------------------------
    def compare(self, baseline_curve, new_curve):

        base = self.simulate_championship_curve(baseline_curve)
        new = self.simulate_championship_curve(new_curve)

        return {
            "baseline_avg_title_odds": base["avg_championship_odds"],
            "new_avg_title_odds": new["avg_championship_odds"],
            "delta": new["avg_championship_odds"] - base["avg_championship_odds"],
            "baseline_best_year": base["best_year"],
            "new_best_year": new["best_year"],
        }
