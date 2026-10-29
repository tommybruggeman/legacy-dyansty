import unittest

from league_ai import acceptance, idea_log


class AcceptanceTest(unittest.TestCase):
    def test_compare_needs_enough_trades(self):
        self.assertIsNone(acceptance.compare([0.9, 1.0], 0.95))
        shares = [0.4, 0.6, 0.8, 0.9, 1.0, 1.2, 1.5]
        self.assertEqual(acceptance.compare(shares, 0.85), "3 of 7 sides of real trades here got this share or less")
        self.assertIsNone(acceptance.compare(shares, None))

    def test_settled_by_share(self):
        rows = [{"result": {"status": "happened", "ideas": [{"their_share": {"close": 1.02}}]}},
                {"result": {"status": "not_made", "ideas": [{"their_share": {"close": 0.9}}, {"their_share": {"close": 1.05}}]}},
                {"result": {"status": "open", "ideas": [{"their_share": {"close": 1.0}}]}}]
        self.assertEqual(acceptance.settled_by_share(rows), {"0.85-1.00": {"happened": 0, "not_made": 1}, "1.00-1.15": {"happened": 1, "not_made": 1}})

    def test_idea_log_keeps_shares(self):
        out = {"ideas": [{"partner": "P", "you_get": ["A (WR1)"], "open_with": ["B (WR9)"], "their_share": {"open": 0.86, "close": 1.0, "walk": 1.0}}]}
        self.assertEqual(idea_log.compact(out)[0]["their_share"]["open"], 0.86)



class OwnerRateTest(unittest.TestCase):
    def test_owner_record_pulled_toward_league(self):
        by_owner = {"A": [0.5, 0.6, 0.7, 0.8], "B": [1.2, 1.3, 1.4, 1.5]}
        league = acceptance.owner_rate(by_owner, "nobody", 1.0)
        self.assertAlmostEqual(league, 0.5)
        self.assertGreater(acceptance.owner_rate(by_owner, "a", 1.0), league)
        self.assertLess(acceptance.owner_rate(by_owner, "B", 1.0), league)
        self.assertIsNone(acceptance.owner_rate({"A": [1.0]}, "A", 1.0))


if __name__ == "__main__":
    unittest.main()
