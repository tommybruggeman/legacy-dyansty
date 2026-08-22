
import numpy as np
import pandas as pd

class LeagueSimulationEngine:

    def simulate_team_week(self, roster_df, weekly_engine):
        """
        Simulates full roster weekly score distribution.
        """

        results = []

        for _, player in roster_df.iterrows():

            sim = weekly_engine.simulate_player_week(player)

            results.append(sim["mean"])

        team_score = sum(results)

        return {
            "team_mean": float(team_score),
            "player_count": len(results)
        }

    def simulate_matchup(self, team_a, team_b, weekly_engine, sims=1000):

        wins = 0

        for _ in range(sims):

            a_score = 0
            b_score = 0

            for _, p in team_a.iterrows():
                a_score += weekly_engine.simulate_player_week(p)["mean"] + np.random.normal(0, 3)


            for _, p in team_b.iterrows():
                b_score += weekly_engine.simulate_player_week(p)["mean"] + np.random.normal(0, 3)


            if a_score > b_score:
                wins += 1

        return {
            "team_a_win_prob": wins / sims,
            "team_b_win_prob": 1 - (wins / sims)
        }
