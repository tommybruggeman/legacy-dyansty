
from snapshot.context.player_context_engine import PlayerContextEngine
import numpy as np


import numpy as np

class WeeklyScoringEngine:

    def __init__(self):
        self.context_engine = PlayerContextEngine()


    def simulate_player_week(self, player):
        """
        Produces full distribution of weekly fantasy outcomes.
        """

        # -----------------------------
        # CONTEXT-AWARE SCORING LAYER
        # -----------------------------

        ctx = self.context_engine.build_context(player)

        context_multiplier = (
            ctx["snap_share"] * 0.35 +
            ctx["target_share"] * 0.25 +
            ctx["red_zone_share"] * 0.20 +
            ctx["scheme_fit"] * 0.10 +
            (1 - ctx["injury_risk"]) * 0.10
        )

        matchup_factor = 1 + (0.5 - ctx["opponent_defense_rank"] / 32)

        # CONTEXT-AWARE OVERRIDE (NEW CORE LAYER)
        if "context_score" in player:
            base = player["context_score"]
        else:
            base = player.get("projected_points", 15)
        volatility = player.get("volatility", 0.25)

        sims = []

        for _ in range(1000):

            noise = np.random.normal(0, volatility * 10)

            value = base * context_multiplier * matchup_factor + noise

            sims.append(value)
        volatility = player.get("volatility", 0.25)
        matchup = player.get("matchup_modifier", 1.0)

        sims = []

        for _ in range(1000):

            noise = np.random.normal(0, volatility * 10)

            value = base * matchup + noise

            sims.append(value)

        sims = np.array(sims)

        
        # -----------------------------
        # FEEDBACK RECORD HOOK
        # -----------------------------

        self.last_simulation = {
            "mean": float(np.mean(sims)),
            "floor": float(np.percentile(sims, 10)),
            "ceiling": float(np.percentile(sims, 90)),
        }

        return {
            "mean": float(np.mean(sims)),
            "floor": float(np.percentile(sims, 10)),
            "ceiling": float(np.percentile(sims, 90)),
            "boom_prob": float(np.mean(sims > base * 1.5)),
            "volatility": float(np.std(sims))
        }

    def compare_players(self, p1, p2):

        s1 = self.simulate_player_week(p1)
        s2 = self.simulate_player_week(p2)

        
        # -----------------------------
        # FEEDBACK RECORD HOOK
        # -----------------------------

        self.last_simulation = {
            "mean": float(np.mean(sims)),
            "floor": float(np.percentile(sims, 10)),
            "ceiling": float(np.percentile(sims, 90)),
        }

        return {
            "player_1": s1,
            "player_2": s2,
            "advantage": "player_1" if s1["mean"] > s2["mean"] else "player_2"
        }



    def update_weights(self, feedback_df):
        """
        Simple adaptive correction system (first ML step)
        """

        mean_error = feedback_df["error"].mean()

        # adaptive correction signals (NOT hard rules, soft drift)
        self.bias_correction = getattr(self, "bias_correction", 1.0)

        if mean_error > 2:
            self.bias_correction *= 0.98  # overpredicting
        elif mean_error < -2:
            self.bias_correction *= 1.02  # underpredicting

        return {
            "updated_bias": self.bias_correction,
            "mean_error": float(mean_error)
        }
