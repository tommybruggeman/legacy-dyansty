
import numpy as np

class FeatureRealityEngine:

    def build(self, player):

        return {
            "snap_share": player.get("snap_share", 0.7),
            "target_share": player.get("target_share", 0.2),
            "route_share": player.get("route_share", 0.6),

            "offense_strength": player.get("offense_strength", 1.0),
            "qb_quality": player.get("qb_quality", 1.0),

            "positional_difficulty": player.get("positional_difficulty", 1.0),

            "context_score": (
                player.get("projected_points", 15)
                * player.get("snap_share", 0.7)
                * player.get("offense_strength", 1.0)
                * player.get("qb_quality", 1.0)
                * (2.0 - player.get("positional_difficulty", 1.0))
            )
        }
