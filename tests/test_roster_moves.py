import unittest
from types import SimpleNamespace

from league_ai.roster_moves import Rules, cut_or_keep, dead_cap, ir_choice, plan, rookie_option, rules_from, taxi_choice


class DeadCapTest(unittest.TestCase):
    def test_percent_of_each_remaining_season_and_free_dollar_deals(self):
        rules = Rules(dead_cap_pct=50)
        self.assertEqual(dead_cap(30, 3, rules), (15.0, 45.0))
        self.assertEqual(dead_cap(1, 1, rules), (0.0, 0.0))
        self.assertEqual(dead_cap(1, 1, Rules(one_dollar_free=False)), (0.5, 0.5))

    def test_rules_come_from_settings(self):
        ai = SimpleNamespace(one_dollar_deals_no_dead_cap=True, ir_limit=1, ir_cap_fraction=0.5, taxi_limit=1, taxi_cap_fraction=2 / 3,
                             taxi_rookies_only=True, taxi_skips_contract_year=True)
        r = rules_from({"default_dead_cap_pct": 25, "rookie_option_years": 2}, ai)
        self.assertEqual((r.dead_cap_pct, r.taxi_fraction, r.option_years), (25.0, 2 / 3, 2))


class CutTest(unittest.TestCase):
    def test_aging_overpaid_back_is_cut_after_the_season_not_mid_season(self):
        r = cut_or_keep(name="Old RB", pos="RB", age=29, salary=30, years_left=3, fair_now=20, rules=Rules(), offseason=False, season_left=0.4)
        self.assertEqual(r["call"], "cut after this season, before the auction")
        self.assertEqual(r["dead_cap_per_season"], 15.0)

    def test_young_bargain_is_kept(self):
        r = cut_or_keep(name="Young WR", pos="WR", age=23, salary=10, years_left=3, fair_now=25, rules=Rules(), offseason=True)
        self.assertEqual(r["call"], "keep")

    def test_dead_weight_is_cut_now_in_the_offseason(self):
        r = cut_or_keep(name="Done TE", pos="TE", age=32, salary=20, years_left=2, fair_now=1, rules=Rules(), offseason=True)
        self.assertEqual(r["call"], "cut now")


class TaxiIrOptionTest(unittest.TestCase):
    def test_taxi_prefers_the_raw_rookie_over_the_day_one_starter(self):
        out = taxi_choice([{"name": "Raw WR", "pos": "WR", "age": 21, "salary": 12, "years": 2, "fair_now": 4},
                           {"name": "Starter RB", "pos": "RB", "age": 22, "salary": 4, "years": 2, "fair_now": 15}], Rules())
        self.assertEqual(out["call"], "taxi Raw WR")

    def test_no_extra_year_when_the_league_does_not_skip_one(self):
        out = taxi_choice([{"name": "A", "pos": "WR", "age": 21, "salary": 4, "years": 2, "fair_now": 20}], Rules(taxi_skips_year=False))
        self.assertEqual(out["candidates"][0]["extra_year_value"], 0.0)

    def test_ir_goes_to_the_biggest_cap_credit(self):
        out = ir_choice([{"name": "A", "injury": "IR", "salary": 30}, {"name": "B", "injury": "Out", "salary": 50},
                         {"name": "C", "injury": None, "salary": 80}], Rules())
        self.assertEqual(out["call"], "IR B")
        self.assertEqual(out["candidates"][0]["cap_credit"], 25.0)

    def test_option_picked_up_only_when_worth_beats_the_price(self):
        self.assertEqual(rookie_option(name="X", pos="WR", age=23, rookie_round=1, fair_now=40, rules=Rules())["call"], "pick up the option")
        self.assertEqual(rookie_option(name="Y", pos="RB", age=25, rookie_round=1, fair_now=10, rules=Rules())["call"], "let him walk")


class PlanTest(unittest.TestCase):
    def test_plan_lists_cuts_and_taxi_for_one_team(self):
        def p(sid, pos, age, salary, years, **kw):
            return SimpleNamespace(sleeper_id=sid, name=sid, position=pos, age=age, salary=salary, years_left=years, team_id="T",
                                   injury=kw.get("injury"), designation=None, contract_type=kw.get("ct"), rookie_round=kw.get("rnd"),
                                   rookie_year=kw.get("ry"))
        players = [p("bad", "TE", 32, 20, 2), p("good", "WR", 24, 5, 2), p("rook", "WR", 21, 8, 2, ct="rookie", rnd=1, ry=2027)]
        out = plan(players, "T", Rules(), offseason=True, season=2026, fair_cap={"bad": 1, "good": 30, "rook": 3})
        self.assertEqual([c["player"] for c in out["cut_candidates"]], ["bad"])
        self.assertEqual(out["taxi"]["call"], "taxi rook")
        self.assertEqual(out["mode"], "offseason")


if __name__ == "__main__":
    unittest.main()
