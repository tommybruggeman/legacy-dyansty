
import pandas as pd

class PlayerContextEngine:

    def build_context(self, player_row):
        return {
            "sleeper_id": player_row.get("sleeper_id"),
            
            # usage
            "snap_share": player_row.get("snap_share", 0.5),
            "target_share": player_row.get("target_share", 0.2),
            "red_zone_share": player_row.get("red_zone_share", 0.1),

            # environment
            "team_offensive_rank": player_row.get("team_offensive_rank", 16),
            "opponent_defense_rank": player_row.get("opponent_defense_rank", 16),

            # risk
            "injury_risk": player_row.get("injury_risk", 0.1),
            "fatigue": player_row.get("fatigue", 0.1),

            # scheme
            "scheme_fit": player_row.get("scheme_fit", 0.5),

            # momentum
            "usage_trend": player_row.get("usage_trend", 0.0),
        }

    def build_batch(self, players_df):
        return [self.build_context(p) for p in players_df.to_dict("records")]
