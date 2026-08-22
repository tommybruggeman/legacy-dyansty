
class ScoringEngine:

    def __init__(self, settings=None):

        # DEFAULT (your current test rules)
        self.settings = settings or {
            "pass_yards_per_point": 25,
            "pass_td": 4,
            "interception": -2,
            "rush_yards_per_point": 10,
            "rush_td": 6,
            "reception": 1,
            "fumble": -2
        }

    def score_qb(self, stats):
        return (
            stats.get("pass_yards", 0) / self.settings["pass_yards_per_point"]
            + stats.get("pass_tds", 0) * self.settings["pass_td"]
            + stats.get("interceptions", 0) * self.settings["interception"]
            + stats.get("rush_yards", 0) / self.settings["rush_yards_per_point"]
            + stats.get("rush_tds", 0) * self.settings["rush_td"]
            + stats.get("fumbles", 0) * self.settings["fumble"]
        )

    def score_skill_position(self, stats):
        return (
            stats.get("rush_yards", 0) / self.settings["rush_yards_per_point"]
            + stats.get("receiving_yards", 0) / self.settings["rush_yards_per_point"]
            + stats.get("receptions", 0) * self.settings["reception"]
            + stats.get("rush_tds", 0) * self.settings["rush_td"]
            + stats.get("receiving_tds", 0) * self.settings["rush_td"]
            + stats.get("fumbles", 0) * self.settings["fumble"]
        )

    def score(self, player):
        pos = player.get("pos", "RB")

        if pos == "QB":
            return self.score_qb(player)
        else:
            return self.score_skill_position(player)
