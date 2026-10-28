import unittest

from league_ai import scorecard
from league_ai.start_sit_grade import grade


def _lineup(season, week, owner, starter, bench):
    return {"season": season, "week": week, "owner_name": owner,
            "details": {"starters": [{"id": starter[0], "pos": "WR", "pts": starter[1]}], "bench": [{"id": bench[0], "pos": "WR", "pts": bench[1]}]}}


class ScorecardTest(unittest.TestCase):
    def test_build_and_owner_view(self):
        rows = [_lineup(2026, 1, "Ann", ("a", 5), ("b", 12)), _lineup(2026, 2, "Ann", ("a", 15), ("b", 4)), _lineup(2026, 1, "Bo", ("c", 9), ("d", 3))]
        proj = {"a": 8.0, "b": 10.0, "c": 12.0, "d": 6.0}
        card = scorecard.build(grade(rows, lambda s, w: proj))
        self.assertEqual(card["season"], 2026)
        self.assertEqual(card["overall"]["calls"], 3)
        self.assertEqual([w["week"] for w in card["by_week"]], [1, 2])
        self.assertEqual(card["by_position"][0]["pos"], "WR")
        view = scorecard.for_owner(card, "ann")
        self.assertEqual(view["you"]["calls"], 2)
        self.assertNotIn("owners", view)   # other owners' lines stay private
        self.assertIsNone(scorecard.for_owner({}, "Ann"))


if __name__ == "__main__":
    unittest.main()
