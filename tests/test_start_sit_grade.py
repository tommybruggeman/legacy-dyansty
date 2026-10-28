import unittest

from league_ai.start_sit_grade import grade


def lineup(season, week, starters, bench):
    return {"season": season, "week": week, "owner_name": "a",
            "details": {"starters": [{"id": i, "pos": "WR", "pts": p} for i, p in starters],
                        "bench": [{"id": i, "pos": "WR", "pts": p} for i, p in bench]}}


class StartSitGradeTest(unittest.TestCase):
    def test_projection_beats_owner_when_owner_starts_the_wrong_player(self):
        rows = [lineup(2025, w, [("x", 5.0)], [("y", 15.0)]) for w in range(1, 5)]
        res = grade(rows, lambda s, w: {"x": 8.0, "y": 12.0})
        self.assertEqual(res["all"]["owner"]["right"], 0.0)
        self.assertEqual(res["all"]["projection"]["right"], 1.0)
        self.assertEqual(res["engine_vs_owner_when_they_disagree"]["calls"], 4)
        self.assertEqual(res["engine_vs_owner_when_they_disagree"]["points_per_call"], 10.0)
        # season averages only exist after week 1, so they grade 3 calls
        self.assertEqual(res["all"]["season_avg"]["calls"], 3)

    def test_out_players_are_not_calls(self):
        rows = [lineup(2025, 1, [("x", 5.0)], [("y", 0.0)])]
        res = grade(rows, lambda s, w: {"x": 8.0, "y": 0.0})
        self.assertNotIn("all", res)

    def test_tilt_can_flip_a_close_call(self):
        rows = [lineup(2025, 1, [("x", 5.0)], [("y", 9.0)])]
        res = grade(rows, lambda s, w: {"x": 10.0, "y": 9.5}, tilt=lambda s, w, pid: 1.1 if pid == "y" else 0.95)
        self.assertEqual(res["all"]["projection"]["right"], 0.0)
        self.assertEqual(res["all"]["projection_matchup"]["right"], 1.0)
        self.assertIn("close", res)


if __name__ == "__main__":
    unittest.main()
