
import numpy as np

class ContextEngine:

    def enrich_player(self, player):
        return {
            "age": player.get("age", 26),

            # PLAYER CONTEXT
            "volatility": player.get("volatility", 0.25),
            "injury_risk": player.get("injury_risk", 0.1),

            # USAGE CONTEXT
            "projected_points": player.get("projected_points", 15),
            "target_share": player.get("target_share", 0.2),
            "snap_share": player.get("snap_share", 0.7),

            # ENVIRONMENT CONTEXT
            "matchup_modifier": player.get("matchup_modifier", 1.0),
            "home_boost": player.get("home_boost", 1.0),
            "weather_penalty": player.get("weather_penalty", 0.0),

            # TEAM CONTEXT
            "offense_strength": player.get("offense_strength", 1.0),
            "qb_quality": player.get("qb_quality", 1.0),

            # DERIVED SIGNAL
            "context_score": (
                player.get("projected_points", 15)
                * player.get("matchup_modifier", 1.0)
                * player.get("offense_strength", 1.0)
                * player.get("qb_quality", 1.0)
                * player.get("snap_share", 0.7)
                * (1 - player.get("injury_risk", 0.1))
            )
        }

    def enrich_team(self, team_df):
        return [
            self.enrich_player(p)
            for _, p in team_df.iterrows()
        ]
