import time
import unittest

from league_ai.front_office import lineup_moves, short_matchup
from league_ai.playoff_odds import OddsModel, simulate, standings_through, trade_odds, week_spread
from league_ai.trade_finder import trade_finder_tools
from tests.test_trade_finder import _league

TEAMS = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]


def _round_robin(weeks):
    out = []
    for w in range(weeks):
        order = TEAMS[:1] + TEAMS[1:][w % 9:] + TEAMS[1:][:w % 9]
        out.append([(order[i], order[-1 - i]) for i in range(5)])
    return out


class SimulateTests(unittest.TestCase):
    def test_odds_add_up_to_the_playoff_spots(self):
        odds = simulate({t: (0, 0) for t in TEAMS}, {t: 100 for t in TEAMS}, _round_robin(6), sims=2000)
        self.assertAlmostEqual(sum(odds.values()), 4.0, places=6)
        for p in odds.values():
            self.assertTrue(0.25 < p < 0.55)  # even teams land near 40%

    def test_a_better_team_and_a_lead_both_help(self):
        means = {t: 100 for t in TEAMS} | {"A": 125}
        odds = simulate({t: (0, 0) for t in TEAMS}, means, _round_robin(6), sims=2000)
        self.assertGreater(odds["A"], 0.75)
        table = {t: (0, 0) for t in TEAMS} | {"B": (20, 900)}
        odds = simulate(table, {t: 100 for t in TEAMS}, _round_robin(3), sims=2000)
        self.assertGreater(odds["B"], 0.95)

    def test_no_games_left_is_just_the_standings(self):
        table = {t: (i, 100 * i) for i, t in enumerate(TEAMS)}
        odds = simulate(table, {t: 100 for t in TEAMS}, [], sims=50)
        self.assertEqual({t for t, p in odds.items() if p == 1.0}, {"G", "H", "I", "J"})

    def test_fast_enough_for_a_page_load(self):
        start = time.perf_counter()
        simulate({t: (0, 0) for t in TEAMS}, {t: 100 for t in TEAMS}, _round_robin(8))
        self.assertLess(time.perf_counter() - start, 3.0)


class ModelTests(unittest.TestCase):
    def _rows(self, weeks):
        rows = []
        for w, games in enumerate(_round_robin(weeks), start=1):
            for a, b in games:
                rows.append({"owner_name": a.lower(), "week": w, "points": 110 + w, "standing_points": 3, "is_playoff": False})
                rows.append({"owner_name": b, "week": w, "points": 90 - w, "standing_points": 0, "is_playoff": False})
        return rows

    def test_standings_and_spread(self):
        rows = self._rows(3)
        table = standings_through(rows, 2)
        self.assertEqual(table["a"], (6.0, 223.0))  # raw names; OddsModel unifies them
        self.assertEqual(week_spread(rows[:10]), 25.0)  # too few games to measure

    def test_names_unify_and_week_in_progress_is_simulated(self):
        asked = []

        def schedule(weeks):
            asked.append(weeks)
            return _round_robin(14)[weeks.start - 1:weeks.stop - 1]
        model = OddsModel(self._rows(8), {t: 100 for t in TEAMS}, week=8, playoff_start=15, schedule_for=schedule)
        self.assertEqual(model.last_final, 7)  # week 8 rows exist but the week is still being played
        self.assertEqual(asked, [range(7, 15)])
        self.assertEqual(len(model.remaining()), 7)
        summary = model.summary()
        self.assertEqual(set(summary["odds"]), set(TEAMS))  # 'a' from the history folded into 'A'
        self.assertIn("A", summary["change"])

    def test_trade_odds_moves_both_teams(self):
        model = OddsModel([], {t: 100 for t in TEAMS}, week=5, playoff_start=15, schedule_for=lambda w: _round_robin(14)[:len(w)])
        out = trade_odds(model, "A", "B", 12.0, -12.0)
        self.assertGreater(out["you"]["after"], out["you"]["before"])
        self.assertLess(out["B"]["after"], out["B"]["before"])


class TradeToolTests(unittest.TestCase):
    def test_evaluate_trade_includes_playoff_odds(self):
        league = _league()
        names = [t["owner_name"] for t in league.teams.values()]
        model = OddsModel([], {n: 100 for n in names}, week=5, playoff_start=12,
                          schedule_for=lambda w: [[(names[0], names[1]), (names[2], names[3])]] * len(w))
        tools = {t.name: t for t in trade_finder_tools(lambda: (league, {t: 50.0 for t in league.teams}, "me"), odds=lambda: model)}
        out = tools["evaluate_trade"].fn(partner="Deepwr", you_get=["WR deepwr-wr0"], you_send=["RB me-rb0"])
        self.assertIn("playoff_odds", out)
        self.assertIn("you", out["playoff_odds"])
        self.assertGreater(out["playoff_odds"]["you"]["weekly_points_change"], 0)
        self.assertIn("odds", tools["get_playoff_odds"].fn())

    def test_no_odds_tool_without_a_model(self):
        league = _league()
        names = {t.name for t in trade_finder_tools(lambda: (league, {}, "me"))}
        self.assertNotIn("get_playoff_odds", names)


class MovesTests(unittest.TestCase):
    BEST = [{"id": "1", "name": "Justin Jefferson", "pos": "WR", "slot": "WR", "matchup": "vs DET, soft: DET allows 4th-most PPR to WR (20/g)"},
            {"id": "2", "name": "Keon Coleman", "pos": "WR", "slot": "FLEX", "matchup": None},
            {"id": "3", "name": "QB Guy", "pos": "QB", "slot": "QB", "matchup": "@ SF"}]

    def test_starts_then_sits_with_reasons(self):
        moves = lineup_moves(self.BEST, ["3", "9", "8"], on_bye=["9"], names={"9": "Dare Ogunbowale", "8": "Tracy"})
        self.assertEqual([m["action"] for m in moves], ["start", "start", "sit", "sit"])
        self.assertEqual(moves[0], {"action": "start", "player": "Justin Jefferson", "slot": "WR", "note": "vs DET · soft"})
        self.assertEqual(moves[1]["slot"], "FLEX")
        self.assertEqual(moves[2]["note"], "bye")
        self.assertEqual(moves[3]["note"], "better option on bench")

    def test_set_lineup_and_unknown_week(self):
        self.assertEqual(lineup_moves(self.BEST, ["1", "2", "3"]), [])
        self.assertEqual(lineup_moves(self.BEST, None), [])

    def test_short_matchup(self):
        self.assertEqual(short_matchup("@ SF"), "@ SF")
        self.assertEqual(short_matchup("vs DET, team implied 27 pts"), "vs DET")
        self.assertEqual(short_matchup(None), "")


if __name__ == "__main__":
    unittest.main()


class BacktestTests(unittest.TestCase):
    def test_backtest_scores_a_finished_season(self):
        from league_ai.playoff_odds import backtest_season, score_backtest
        rows = []
        strength = {t: 80 + 5 * i for i, t in enumerate(TEAMS)}  # J best, A worst
        for w, games in enumerate(_round_robin(9), start=1):
            for a, b in games:
                pa, pb = strength[a] + (w % 3), strength[b] - (w % 2)
                for me, them, mine, theirs in ((a, b, pa, pb), (b, a, pb, pa)):
                    rows.append({"week": w, "owner_name": me, "opponent_name": them, "points": mine, "standing_points": 2 if mine > theirs else 0, "is_playoff": False})
        preds = backtest_season(rows, sims=300)
        self.assertEqual(len(preds), 8 * len(TEAMS))
        score = score_backtest(preds)
        self.assertLess(score["brier"], 0.15)  # a league this lopsided is easy to call
        self.assertIn("baseline_brier", score)
        self.assertTrue(score["calibration"])


class PickupToolTests(unittest.TestCase):
    def test_pickup_raises_odds_when_he_starts(self):
        from league_ai.power import PlayerStrength
        league = _league()
        names = [t["owner_name"] for t in league.teams.values()]
        model = OddsModel([], {n: 100 for n in names}, week=5, playoff_start=12,
                          schedule_for=lambda w: [[(names[0], names[1]), (names[2], names[3])]] * len(w))
        fa = PlayerStrength(sleeper_id="fa1", name="Free Guy", position="WR", team_id="me", owner="Me", now=19, dynasty=12, available=True)
        tools = {t.name: t for t in trade_finder_tools(lambda: (league, {t: 50.0 for t in league.teams}, "me"), odds=lambda: model,
                                                       free_agent=lambda n: fa if "free" in n.lower() else None)}
        out = tools["get_pickup_odds"].fn(player="Free Guy", drop="WR me-wr2", bid=10)
        self.assertGreater(out["playoff_odds"]["weekly_points_change"], 0)
        self.assertGreaterEqual(out["playoff_odds"]["after"], out["playoff_odds"]["before"])
        self.assertIn("odds_per_dollar", out)
        self.assertIn("error", tools["get_pickup_odds"].fn(player="Nobody"))


class MeanSpreadTest(unittest.TestCase):
    def test_unsure_means_pull_early_odds_toward_the_middle(self):
        from league_ai.playoff_odds import mean_sd, simulate

        teams = [f"t{i}" for i in range(8)]
        means = {t: 100 + 6 * i for i, t in enumerate(teams)}
        schedule = [[(teams[i], teams[(i + w + 1) % 8]) for i in range(0, 8, 2)] for w in range(12)]
        sure = simulate({}, means, schedule, sims=1500)
        humble = simulate({}, means, schedule, sims=1500, mean_spread=mean_sd(25.0, 1))
        self.assertLess(humble["t7"], sure["t7"])
        self.assertGreater(humble["t0"], sure["t0"])
        self.assertLess(mean_sd(25.0, 10), mean_sd(25.0, 1))
