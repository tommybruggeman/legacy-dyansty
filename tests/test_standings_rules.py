import unittest

from services.standings_rules import last_completed_week, score_week


def _by_team(rows):
    return {row["team"]: row for row in rows}


class LastCompletedWeekTest(unittest.TestCase):
    def test_uses_league_last_scored_leg(self):
        league = {"settings": {"last_scored_leg": 3}}
        self.assertEqual(last_completed_week(league, {"week": 4}), 3)

    def test_falls_back_to_week_before_current(self):
        self.assertEqual(last_completed_week({"settings": {}}, {"week": 4}), 3)

    def test_preseason_has_no_finished_weeks(self):
        self.assertEqual(last_completed_week(None, {"week": 0}), 0)
        self.assertEqual(last_completed_week(None, None), 0)


class ScoreWeekTest(unittest.TestCase):
    def test_win_loss_from_head_to_head_and_top_five_separate(self):
        # A loser can still finish top 5; a winner can miss it.
        results = [
            ("A", 150.0, 140.0),
            ("B", 140.0, 150.0),
            ("C", 130.0, 120.0),
            ("D", 120.0, 130.0),
            ("E", 110.0, 100.0),
            ("F", 100.0, 110.0),
            ("G", 90.0, 80.0),
            ("H", 80.0, 90.0),
        ]
        out = _by_team(score_week(results))

        self.assertEqual((out["B"]["win"], out["B"]["loss"], out["B"]["top5"]), (0, 1, 1))
        self.assertEqual(out["B"]["standing_points"], 1)
        self.assertEqual((out["G"]["win"], out["G"]["top5"]), (1, 0))
        self.assertEqual(out["G"]["standing_points"], 2)
        self.assertEqual(out["A"]["standing_points"], 3)
        self.assertEqual(sum(row["top5"] for row in out.values()), 5)

    def test_tie_is_neither_win_nor_loss(self):
        out = _by_team(score_week([("A", 0.0, 0.0), ("B", 0.0, 0.0)]))
        self.assertEqual((out["A"]["win"], out["A"]["loss"], out["A"]["tie"]), (0, 0, 1))


class LeagueWeeksOneToThreeTest(unittest.TestCase):
    """2026 weeks 1-3 as scored by Sleeper; week 4 was in progress."""

    WEEKS = [
        [(1, "Dburruel", 164.38), (1, "nandonio", 106.26), (2, "chaseseyforth", 177.16),
         (2, "RollPads", 124.72), (3, "chaychayy", 68.88), (3, "kevinwells33", 104.06),
         (4, "tommybruggeman", 153.66), (4, "gmoney38", 116.36), (5, "MekelS", 125.86),
         (5, "ConnorZar", 113.14)],
        [(1, "chaseseyforth", 131.38), (1, "nandonio", 123.34), (2, "Dburruel", 89.82),
         (2, "kevinwells33", 141.96), (3, "RollPads", 156.9), (3, "gmoney38", 138.9),
         (4, "chaychayy", 92.3), (4, "ConnorZar", 119.0), (5, "tommybruggeman", 132.0),
         (5, "MekelS", 74.28)],
        [(1, "nandonio", 95.3), (1, "kevinwells33", 138.24), (2, "chaseseyforth", 146.8),
         (2, "gmoney38", 135.84), (3, "Dburruel", 118.56), (3, "ConnorZar", 112.14),
         (4, "RollPads", 123.3), (4, "MekelS", 106.2), (5, "tommybruggeman", 109.06),
         (5, "chaychayy", 104.9)],
    ]

    def test_chase_three_and_oh_one_point_ahead_of_tommy(self):
        totals = {}
        for week in self.WEEKS:
            results = []
            for mid in {m for m, _, _ in week}:
                (ta, sa), (tb, sb) = [(t, s) for m, t, s in week if m == mid]
                results += [(ta, sa, sb), (tb, sb, sa)]
            for row in score_week(results):
                t = totals.setdefault(row["team"], {"W": 0, "L": 0, "T5": 0, "SP": 0})
                t["W"] += row["win"]
                t["L"] += row["loss"]
                t["T5"] += row["top5"]
                t["SP"] += row["standing_points"]

        self.assertEqual(totals["chaseseyforth"], {"W": 3, "L": 0, "T5": 3, "SP": 9})
        self.assertEqual(totals["tommybruggeman"], {"W": 3, "L": 0, "T5": 2, "SP": 8})


if __name__ == "__main__":
    unittest.main()
