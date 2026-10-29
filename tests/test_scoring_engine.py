import unittest

from scoring.engine import full_line, lineup_points, player_points, rule_diff
from scoring.shadow import compare_week, explain, score_season

PPR = {"pass_yd": 0.04, "pass_td": 4, "pass_int": -2, "rush_yd": 0.1, "rush_td": 6, "rec": 1, "rec_yd": 0.1, "rec_td": 6,
       "fum_lost": -2, "bonus_rec_te": 0.5, "bonus_rush_yd_100": 3,
       "pts_allow_0": 10, "pts_allow_7_13": 4, "pts_allow_35p": -4, "sack": 1, "int": 2}


class PlayerPointsTests(unittest.TestCase):
    def test_qb_line(self):
        self.assertEqual(player_points({"pass_yd": 275, "pass_td": 2, "pass_int": 1, "rush_yd": 12}, PPR, "QB"), 18.2)

    def test_te_premium_added_when_feed_lacks_it(self):
        self.assertEqual(player_points({"rec": 6, "rec_yd": 70}, PPR, "TE"), 16.0)
        self.assertEqual(player_points({"rec": 6, "rec_yd": 70}, PPR, "WR"), 13.0)

    def test_feed_bonus_is_not_counted_twice(self):
        self.assertEqual(player_points({"rec": 6, "rec_yd": 70, "bonus_rec_te": 6}, PPR, "TE"), 16.0)

    def test_hundred_yard_bonus(self):
        self.assertEqual(player_points({"rush_yd": 100}, PPR, "RB"), 13.0)
        self.assertEqual(player_points({"rush_yd": 99}, PPR, "RB"), 9.9)

    def test_defense_bucket_from_points_allowed(self):
        self.assertEqual(player_points({"pts_allow": 10, "sack": 3, "int": 1}, PPR, "DEF"), 9.0)
        self.assertEqual(player_points({"pts_allow": 0}, PPR, "DEF"), 10.0)
        self.assertEqual(player_points({"pts_allow": 41}, PPR, "DEF"), -4.0)

    def test_feed_bucket_wins_over_derived(self):
        line = full_line({"pts_allow": 10, "pts_allow_0": 1}, "DEF", PPR)
        self.assertNotIn("pts_allow_7_13", line)

    def test_unknown_and_bad_values_ignored(self):
        self.assertEqual(player_points({"rec": "x", "rec_yd": None, "snaps": 50}, PPR, "WR"), 0.0)
        self.assertEqual(player_points(None, PPR, "WR"), 0.0)

    def test_lineup_skips_empty_slots(self):
        lines = {"1": {"stats": {"rush_yd": 50}, "pos": "RB"}, "2": {"stats": {"rec": 2}, "pos": "WR"}}
        self.assertEqual(lineup_points(["1", "0", "2", "", "9"], lines, PPR), 7.0)

    def test_rule_diff(self):
        self.assertEqual(rule_diff({"rec": 1, "pass_td": 4}, {"rec": 0.5, "pass_td": 4, "sack": 0}), ["rec"])


class ShadowTests(unittest.TestCase):
    lines = {"1": {"stats": {"rec": 5, "rec_yd": 50}, "pos": "TE"}, "2": {"stats": {"rush_yd": 80}, "pos": "RB"}}

    def test_match(self):
        rows = [{"roster_id": 1, "matchup_id": 1, "points": 20.5, "starters": ["1", "2"], "players_points": {"1": 12.5, "2": 8.0}}]
        res = compare_week(rows, self.lines, PPR)
        self.assertEqual((res["teams_match"], res["players_match"], res["misses"]), (1, 2, []))

    def test_miss_names_the_rule(self):
        rows = [{"roster_id": 1, "matchup_id": 1, "points": 18.0, "starters": ["1", "2"], "players_points": {"1": 10.0, "2": 8.0}}]
        res = compare_week(rows, self.lines, PPR)
        self.assertEqual(res["teams_match"], 0)
        self.assertIn("bonus_rec_te", res["misses"][0]["keys"])

    def test_override_uses_custom_points(self):
        rows = [{"roster_id": 1, "matchup_id": 1, "points": 5.0, "custom_points": 20.5, "starters": ["1", "2"], "players_points": {}}]
        res = compare_week(rows, self.lines, PPR)
        self.assertEqual((res["teams_match"], res["overrides"]), (1, 1))

    def test_season_skips_unplayed_weeks(self):
        rows = [{"roster_id": 1, "matchup_id": 1, "points": 20.5, "starters": ["1", "2"], "players_points": {"1": 12.5, "2": 8.0}}]
        res = score_season(2026, lambda w: rows if w == 1 else [{"roster_id": 1, "matchup_id": 1, "points": 0}], lambda w: self.lines, PPR, 3)
        self.assertEqual((res["weeks"], res["teams_match"]), (1, 1))

    def test_explain(self):
        self.assertEqual(explain(-2.0, {"stats": {"fum_lost": 1}}, {"fum_lost": -2, "rec": 1}), ["fum_lost"])


if __name__ == "__main__":
    unittest.main()
