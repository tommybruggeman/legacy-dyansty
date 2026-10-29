import unittest

from league_ai.missing_signals import RedZone, main_passers, ol_out, qb_out, sack_rate


class MissingSignalsTest(unittest.TestCase):
    def test_red_zone_role(self):
        plays = [{"week": 1, "yardline_100": 5, "rush": 1, "rusher_player_id": "rb1", "rush_touchdown": 1},
                 {"week": 1, "yardline_100": 3, "rush": 1, "rusher_player_id": "rb1", "rush_touchdown": 0},
                 {"week": 1, "yardline_100": 50, "rush": 1, "rusher_player_id": "rb2", "rush_touchdown": 0},
                 {"week": 1, "yardline_100": 15, "pass_attempt": 1, "receiver_player_id": "te1", "pass_touchdown": 1}]
        rz = RedZone(plays)
        self.assertAlmostEqual(rz.td_carry, 0.5)
        self.assertGreater(rz.expected_tds("rb1", 0.1), rz.expected_tds("rb2", 0.1))
        self.assertIsNone(rz.expected_tds("nobody", 0.1))

    def test_line_and_backup_qb(self):
        inj = [{"season": 2025, "week": 3, "team": "KC", "position": "T", "report_status": "Out", "gsis_id": "t1"},
               {"season": 2025, "week": 3, "team": "KC", "position": "QB", "report_status": "Doubtful", "gsis_id": "q1"},
               {"season": 2025, "week": 2, "team": "BUF", "position": "G", "report_status": "Out", "gsis_id": "g1"}]
        stats = [{"team": "KC", "position": "QB", "gsis_id": "q1", "attempts": 60, "sacks": 6},
                 {"team": "KC", "position": "QB", "gsis_id": "q2", "attempts": 5, "sacks": 0}]
        self.assertEqual(ol_out(inj, 2025, 3), {"KC": 1})
        self.assertEqual(qb_out(inj, 2025, 3, main_passers(stats)), {"KC"})
        rates, lg = sack_rate(stats)
        self.assertGreater(lg, 0)


if __name__ == "__main__":
    unittest.main()
