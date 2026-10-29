import unittest

from services.trades_view import card, page_html

TRADE = {"id": "1", "season": 2026, "week": 5, "date": "2026-10-05", "owners": ["Grady M", "Tommy B"], "weeks_since": 2,
         "winning": {"owner": "Tommy B", "by": 21.4},
         "sides": [{"owner": "Grady M", "players": [{"player_id": "1", "name": "A <b>", "pos": "QB", "salary": 48, "years": 2, "points_since": 71.2}],
                    "picks": [], "faab": -3, "cap_change": 30, "points_since": 71.2, "market_share": 0.46},
                   {"owner": "Tommy B", "players": [], "picks": [{"label": "2027 R1", "original_owner": "Grady M"}], "faab": 3,
                    "cap_change": -30, "points_since": 92.6, "market_share": 0.54}]}


class TradesViewTests(unittest.TestCase):
    def test_card_shows_verdict_sides_and_value(self):
        out = card(TRADE, "Tommy B")
        self.assertIn("YOU +21", out)
        self.assertIn("You get", out)
        self.assertIn("2027 R1 (Grady M.'s)", out)
        self.assertIn("+$30.0 cap", out)
        self.assertIn("46% / 54%", out)
        self.assertIn("A &lt;b&gt;", out)  # names are escaped
        self.assertIn("tf-card mine", out)

    def test_too_early_hides_points(self):
        t = {**TRADE, "weeks_since": 0, "winning": None}
        out = card(t, None)
        self.assertIn("TOO EARLY", out)
        self.assertNotIn("pts since", out)

    def test_empty_page(self):
        self.assertIn("No trades here yet.", page_html([], None, 0))


if __name__ == "__main__":
    unittest.main()
