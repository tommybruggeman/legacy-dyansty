import unittest

from league_ai.league_points import weekly_points
from league_ai.runtime import season_mode

RULES = {"rec": 1, "rec_yd": 0.1, "rec_td": 6, "bonus_rec_te": 0.5, "rush_yd": 0.1}


class LeaguePointsTests(unittest.TestCase):
    def test_scores_our_saved_lines_with_league_rules(self):
        lines = {
            1: {"te1": {"pos": "TE", "stats": {"gp": 1, "rec": 4, "rec_yd": 50}}, "wr1": {"pos": "WR", "stats": {"gp": 1, "rec": 4, "rec_yd": 50}}},
            2: {"te1": {"pos": "TE", "stats": {"gp": 0}}, "wr1": {"pos": "WR", "stats": {"gp": 1, "rec_td": 1}}},
        }
        out = weekly_points(None, "lg", 2026, ["te1", "wr1"], scoring=RULES, lines=lines)
        self.assertEqual(out["te1"], [(1, 11.0)])  # TE catch bonus counts; week 2 he didn't play
        self.assertEqual(out["wr1"], [(1, 9.0), (2, 6.0)])

    def test_no_rules_or_no_lines_means_caller_keeps_its_fallback(self):
        self.assertEqual(weekly_points(None, "lg", 2026, ["x"], scoring={}, lines={1: {}}), {})
        self.assertEqual(weekly_points(None, "lg", 2026, ["x"], scoring=RULES, lines={}), {})


class SeasonModeTests(unittest.TestCase):
    def test_offseason_from_our_schedule(self):
        import league_ai.runtime as rt

        games_open = [{"week": 17, "home_score": 20}, {"week": 18, "home_score": None}]
        games_done = [{"week": 17, "home_score": 20}, {"week": 18, "home_score": 24}]
        orig = rt.season_games
        try:
            rt.season_games = lambda client, season: games_open
            self.assertEqual(season_mode(None, 18, 15, client=object(), season=2026)[0], False)
            self.assertEqual(rt.current_week(object(), 2026), 18)
            rt.season_games = lambda client, season: games_done
            self.assertEqual(season_mode(None, 18, 15, client=object(), season=2026)[0], True)
            self.assertEqual(rt.current_week(object(), 2026), 18)
        finally:
            rt.season_games = orig


if __name__ == "__main__":
    unittest.main()
