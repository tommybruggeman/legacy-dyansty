import unittest
from types import SimpleNamespace

from league_ai.future_plan import Worth, best_length, lineup_worth, pay_up_leans, plan
from league_ai.roster_moves import Rules
from league_ai.routing import effort_for, topic

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]


def P(pid, team, pos, age, salary, years, fair=None, **kw):
    return SimpleNamespace(sleeper_id=pid, name=f"P{pid}", position=pos, team_id=team, owner=team, age=age, salary=salary,
                           cap_hit=salary, years_left=years, contract_type=kw.get("contract_type", "auction"),
                           rookie_round=kw.get("rookie_round"))


TEAMS = {"A": {"owner_name": "Owner A"}, "B": {"owner_name": "Owner B"}, "C": {"owner_name": "Owner C"}}


def league():
    players = [
        # A: young core signed long, an expiring vet
        P("a1", "A", "QB", 25, 30, 3), P("a2", "A", "WR", 22, 8, 3), P("a3", "A", "RB", 23, 6, 2), P("a4", "A", "WR", 30, 25, 1),
        # B: expiring stars, lots of room next year
        P("b1", "B", "RB", 24, 40, 1), P("b2", "B", "WR", 26, 35, 1), P("b3", "B", "TE", 27, 15, 1), P("b4", "B", "QB", 28, 20, 2),
        # C: mid team; an overpaid declining TE signed two more years; a rookie on his last year
        P("c1", "C", "QB", 31, 25, 2), P("c2", "C", "TE", 33, 30, 3), P("c3", "C", "WR", 23, 5, 1, contract_type="rookie", rookie_round=1),
        P("c4", "C", "RB", 27, 18, 2),
    ]
    fair = {"a1": 30, "a2": 22, "a3": 10, "a4": 14, "b1": 38, "b2": 33, "b3": 14, "b4": 20, "c1": 20, "c2": 3, "c3": 30, "c4": 12}
    return players, fair


class FuturePlanTest(unittest.TestCase):
    def setUp(self):
        self.players, self.fair = league()
        self.out = plan(players=self.players, teams=TEAMS, slots=SLOTS, asker="A", season=2026, salary_cap=225, fair=self.fair, rules=Rules(),
                        dead_future={"A": {2027: 10.0}}, picks=[
                            {"draft_year": 2027, "round_number": 1, "original_league_team_id": "A", "current_owner_league_team_id": "B",
                             "current_owner_name": "Owner B", "asset_status": "tradable"},
                            {"draft_year": 2027, "round_number": 2, "original_league_team_id": "C", "original_owner_name": "Owner C",
                             "current_owner_league_team_id": "A", "asset_status": "tradable"}])

    def test_room_by_season_counts_signed_salary_and_dead_cap(self):
        s27 = self.out["seasons"][0]
        self.assertEqual(s27["season"], 2027)
        self.assertEqual(s27["committed"], 44.0)          # a1 30 + a2 8 + a3 6 (a4 expires)
        self.assertEqual(s27["dead_cap"], 10.0)
        self.assertEqual(s27["room"], 225 - 44 - 10)
        self.assertEqual(self.out["seasons"][1]["committed"], 38.0)   # a3 ends after 2027

    def test_next_class_has_expiring_deals_and_cap_casualties_but_not_kept_rookie_options(self):
        cls = self.out["free_agent_classes"][0]
        names = [t["player"] for t in cls["targets"]]
        self.assertIn("Pb1", names)
        self.assertNotIn("Pc3", names)                    # $25 option on a player worth ~$30: owner keeps him
        self.assertTrue(any("Pc3" in x for x in cls["likely_kept_on_rookie_option"]))
        self.assertNotIn("Pa1", names)                    # still signed
        self.assertGreater(len(self.out["free_agent_classes"]), 1)

    def test_targets_carry_price_years_and_rivals(self):
        t = next(t for t in self.out["free_agent_classes"][0]["targets"] if t["player"] == "Pb1")
        self.assertEqual(t["now_with"], "Owner B")
        self.assertGreaterEqual(t["likely_price"], 1)
        self.assertTrue(1 <= t["suggested_years"] <= 4)
        self.assertEqual(t["age_then"], 25)

    def test_picks_owned_and_traded_away(self):
        self.assertEqual(self.out["your_picks"], ["2027 R2 (via Owner C)"])
        self.assertEqual(self.out["your_picks_traded_away"], ["2027 R1 (owned by Owner B)"])

    def test_position_focus(self):
        out = plan(players=self.players, teams=TEAMS, slots=SLOTS, asker="A", season=2026, salary_cap=225, fair=self.fair, rules=Rules(), focus="WR")
        self.assertTrue(all(t["pos"] == "WR" for c in out["free_agent_classes"] for t in c["targets"]))


class PiecesTest(unittest.TestCase):
    def test_young_player_gets_a_longer_deal_than_an_old_one(self):
        young = P("y", "B", "WR", 23, 20, 1)
        old = P("o", "B", "WR", 31, 20, 1)
        filler = [P(f"f{i}", "C", "WR", 26, 30 - 2 * i, 3) for i in range(10)]
        W = Worth([young, old, *filler], {"y": 20.0, "o": 20.0, **{f"f{i}": 30.0 - 2 * i for i in range(10)}})
        self.assertGreater(W(young, 3), W(old, 3))          # the young one climbs the ranks, the old one slides
        self.assertGreater(best_length(young, W, 1, W(young, 1), 4, {}, 2026)[0], best_length(old, W, 1, W(old, 1), 4, {}, 2026)[0])

    def test_lineup_worth_flags_holes(self):
        total, by_pos, holes = lineup_worth([P("q", "A", "QB", 25, 1, 1)], ["QB", "TE"], {"q": 20})
        self.assertEqual((total, holes), (20, ["TE"]))

    def test_pay_up_lean_is_shrunk(self):
        ps = [P(str(i), "A", "RB", 25, 20, 2) for i in range(4)]
        leans = pay_up_leans(ps, {str(i): 10 for i in range(4)})
        self.assertEqual(leans["A"]["RB"], 1.5)           # paid 2x on 4 deals -> halfway to 2x


class RoutingTest(unittest.TestCase):
    def test_restart_is_not_a_lineup_question(self):
        self.assertNotEqual(topic("Restart thought"), "lineup")
        self.assertEqual(topic("start Allen or Shough?"), "lineup")

    def test_multi_season_plans_are_strategy_and_think_hard(self):
        q = "let's make a 2-3 season plan for FA targets so I can be a contender"
        self.assertEqual(topic(q), "strategy")
        self.assertEqual(effort_for(q), "high")


if __name__ == "__main__":
    unittest.main()


class FailedTurnTest(unittest.TestCase):
    def test_unanswered_question_is_saved_so_restart_picks_it_up(self):
        from unittest import mock

        from league_ai import runtime
        from league_ai.client import Trace
        from league_ai.service import AnswerResult

        saved = []
        memory = SimpleNamespace(save_conversation=lambda cid, msgs: saved.append(msgs) or "c1")
        owner = runtime.Owner(user_id="u", league_id="l", league_team_id="t", season=2026, owner_name="O", team_name="T")
        failed = AnswerResult(False, "", Trace(model="m", error_code="timeout"), "timeout")
        with mock.patch.object(runtime, "answer", return_value=failed), mock.patch.object(runtime, "record_usage"), \
                mock.patch.object(runtime, "ClaudeClient"), mock.patch.object(runtime.Owner, "memory", lambda self, c: memory):
            turn = runtime.chat_turn(None, owner, question="Plan my next 3 seasons", history=[], conversation_id=None, pack="", tools=None)
        self.assertEqual(turn.conversation_id, "c1")
        self.assertEqual(saved, [[{"role": "user", "content": "Plan my next 3 seasons"}]])
