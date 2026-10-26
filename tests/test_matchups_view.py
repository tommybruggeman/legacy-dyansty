import unittest

from services.matchups_view import build_games, matchup_cards_html, week_summary


NAMES = {1: "Tommy", 2: "Dylan", 3: "Nando", 4: "Grady", 5: "Chase", 6: "Alex"}


def _rows(scores):
    """scores: {roster_id: (matchup_id, points)}"""
    return [
        {"roster_id": rid, "matchup_id": mid, "points": pts}
        for rid, (mid, pts) in scores.items()
    ]


class MatchupsViewTest(unittest.TestCase):
    def setUp(self):
        self.games = build_games(
            _rows(
                {
                    1: (1, 142.36),
                    2: (1, 118.2),
                    3: (2, 101.0),
                    4: (2, 155.5),
                    5: (3, 120.0),
                    6: (3, 120.0),
                }
            ),
            NAMES,
        )

    def test_ranks_by_score_with_name_tiebreak(self):
        ranks = {
            t["name"]: t["rank"] for g in self.games for t in g["teams"]
        }
        self.assertEqual(ranks["Grady"], 1)
        self.assertEqual(ranks["Tommy"], 2)
        # Alex and Chase tie on 120; name breaks it, like standings.
        self.assertEqual(ranks["Alex"], 3)
        self.assertEqual(ranks["Chase"], 4)
        self.assertEqual(ranks["Nando"], 6)

    def test_top_five_flags(self):
        top5 = {t["name"] for g in self.games for t in g["teams"] if t["top5"]}
        self.assertEqual(top5, {"Grady", "Tommy", "Alex", "Chase", "Dylan"})

    def test_games_ordered_and_leader_first(self):
        self.assertEqual(self.games[0]["teams"][0]["name"], "Grady")
        self.assertEqual(self.games[1]["teams"][0]["name"], "Tommy")

    def test_starters_points_fallback(self):
        games = build_games(
            [
                {"roster_id": 1, "matchup_id": 1, "points": 0, "starters_points": [20.5, 30]},
                {"roster_id": 2, "matchup_id": 1, "points": 12},
            ],
            NAMES,
        )
        self.assertEqual(games[0]["teams"][0]["score"], 50.5)

    def test_summary(self):
        summary = week_summary(self.games)
        self.assertEqual(summary["high"]["name"], "Grady")
        self.assertEqual(summary["cut"]["name"], "Dylan")
        self.assertEqual(summary["closest_margin"], 0)

    def test_unplayed_week_has_no_top5_or_summary(self):
        games = build_games(_rows({1: (1, 0), 2: (1, 0)}), NAMES)
        self.assertFalse(any(t["top5"] for t in games[0]["teams"]))
        self.assertIsNone(week_summary(games))
        self.assertIn("Upcoming", matchup_cards_html(games))

    def test_my_game_first_and_escaped(self):
        names = dict(NAMES)
        names[5] = "<b>Chase</b>"
        games = build_games(_rows({1: (1, 90), 2: (1, 80), 5: (2, 150), 6: (2, 60)}), names)
        out = matchup_cards_html(games, my_roster_id="1", is_final=True)
        self.assertLess(out.index("Your matchup"), out.index('mm-name-text">&lt;b&gt;Chase'))
        self.assertNotIn("<b>Chase</b>", out)
        self.assertIn("Tommy won by 10.00", out)
        self.assertIn("Final", out)

    def test_desktop_hero_and_even_grid(self):
        # 3 games with yours pulled into the hero leaves 2: one full row.
        out = matchup_cards_html(self.games, my_roster_id=1)
        self.assertIn('class="mm-hero"', out)
        self.assertIn("mm-card is-mine phone-only", out)
        self.assertIn("mm-games cols-2", out)
        self.assertIn("in the Top 5 this week", out)

        games = build_games(
            _rows({1: (1, 90), 2: (1, 80), 3: (2, 150), 4: (2, 60), 5: (3, 70), 6: (3, 65)}),
            NAMES,
        )
        out = matchup_cards_html(games, my_roster_id=4)
        self.assertIn("behind the Top 5 line", out)

    def test_no_hero_without_my_team(self):
        out = matchup_cards_html(self.games)
        self.assertNotIn('class="mm-hero"', out)
        self.assertIn("mm-games cols-3", out)


if __name__ == "__main__":
    unittest.main()
