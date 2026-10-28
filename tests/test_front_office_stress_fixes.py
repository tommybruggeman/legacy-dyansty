"""Fixes from the 2026-10-28 Assistant GM stress test: whole rosters, named-target trades, one-line answers, hold calls."""
import unittest
from decimal import Decimal

from league_ai.client import ClaudeClient
from league_ai.context_pack import _roster_table
from league_ai.learning import Ledger
from league_ai.sleeper_roster import NO_CONTRACT, fill_from_sleeper, is_no_contract, missing_rows
from league_ai.tools import ToolRegistry, ToolSpec
from league_ai.trade_finder import find_trades, price_target
from tests.test_league_ai_service import CONFIG, FakeSDK, _resp, _text, _tool_use
from tests.test_trade_finder import _league

STATE = {
    "league_id": "L", "season": 2026,
    "teams": [{"league_team_id": "t1", "owner_name": "Tommy", "team_name": "Tommy"}, {"league_team_id": "t2", "owner_name": "Dylan", "team_name": "Dylan"}],
    "roster": [{"sleeper_player_id": "100", "league_team_id": "t1", "player_name": "Josh Allen", "pos": "QB", "cap_hit": Decimal("48"), "status": "active"}],
}
SLEEPER = [{"roster_id": 1, "players": ["100", "200", "300"], "taxi": ["300"], "reserve": []},
           {"roster_id": 2, "players": ["400"], "taxi": [], "reserve": ["400"]}]
PLAYERS = {"200": {"full_name": "Drake Maye", "position": "QB"}, "300": {"full_name": "Jack Bech", "position": "WR"},
           "400": {"full_name": "Rashee Rice", "position": "WR"}}


class SleeperRosterTests(unittest.TestCase):
    def test_sleeper_players_without_a_contract_join_their_team_at_zero(self):
        rows = missing_rows(STATE, SLEEPER, {1: "t1", 2: "t2"}, PLAYERS)
        by = {r["player_name"]: r for r in rows}
        self.assertEqual(set(by), {"Drake Maye", "Jack Bech", "Rashee Rice"})  # Allen already has a contract row
        self.assertEqual(by["Drake Maye"]["league_team_id"], "t1")
        self.assertEqual(by["Jack Bech"]["roster_designation"], "taxi")
        self.assertEqual(by["Rashee Rice"]["roster_designation"], "ir")
        self.assertTrue(all(is_no_contract(r) and r["cap_hit"] == 0 for r in rows))

    def test_fill_never_breaks_the_state(self):
        def boom(url):
            raise RuntimeError("sleeper down")
        self.assertEqual(fill_from_sleeper(object(), STATE, "sl", "L", get_json=boom)["roster"], STATE["roster"])
        self.assertEqual(fill_from_sleeper(object(), STATE, None, "L")["roster"], STATE["roster"])

    def test_pack_marks_them_as_no_contract(self):
        table = _roster_table([{"player_name": "Drake Maye", "pos": "QB", "cap_hit": Decimal("0"), "status": NO_CONTRACT}])
        self.assertIn("NO CONTRACT", table)
        self.assertNotIn("$0", table)


class NamedTargetTests(unittest.TestCase):
    CAP = {t: 20.0 for t in ("me", "deepwr", "deepboth", "avg")}

    def test_prices_a_named_player_even_from_a_team_with_no_need(self):
        lg = _league()
        # deepboth has no weak spot, so the shortlist never offers its WRs...
        self.assertNotIn("Deepboth", {i["partner"] for i in find_trades(league=lg, asker_team_id="me", cap_space=self.CAP, limit=8)["ideas"]})
        # ...but an owner who names one still gets a price
        out = price_target(league=lg, asker_team_id="me", cap_space=self.CAP, target="WR deepboth-wr0", partner="deepboth")
        self.assertEqual(out["partner"], "Deepboth")
        self.assertIn("fair_deal_found", out)
        if out["fair_deal_found"]:
            self.assertTrue(out["open_with"])
            self.assertGreaterEqual(int(out["by_their_goals"].split("back ")[1].split("%")[0]), 100)
        else:
            self.assertTrue(out["closest_tries"])

    def test_says_so_when_nothing_is_fair(self):
        lg = _league()
        lg.find_player("WR deepwr-wr0").cap_hit = 100.0  # no package of mine can make room for him
        out = price_target(league=lg, asker_team_id="me", cap_space=self.CAP, target="WR deepwr-wr0")
        self.assertFalse(out["fair_deal_found"])
        self.assertEqual(out["note"], "no package is cap-legal for both teams")

    def test_errors_are_plain(self):
        lg = _league()
        self.assertIn("error", price_target(league=lg, asker_team_id="me", cap_space=self.CAP, target="Nobody Here"))
        self.assertIn("already on your roster", price_target(league=lg, asker_team_id="me", cap_space=self.CAP, target="WR me-wr0")["error"])

    def test_no_contract_players_are_left_out_of_deals(self):
        lg = _league()
        for p in lg.by_team["me"]:
            p.no_contract = p.position == "RB"
        out = find_trades(league=lg, asker_team_id="me", cap_space=self.CAP, limit=8)
        self.assertFalse(any(s.startswith("RB") for i in out["ideas"] for s in i["open_with"]))


class ThinAnswerTests(unittest.TestCase):
    def _reg(self):
        reg = ToolRegistry()
        for name in ("get_player_stats", "get_schedule_outlook", "log_prediction"):
            reg.register(ToolSpec(name, name, {"type": "object", "properties": {}}, lambda **kw: {"ok": True}))
        return reg

    def test_one_line_after_research_gets_the_full_answer(self):
        full = "Hold McMillan. He's WR4 this season with a soft playoff schedule, and selling now gets you less than he's worth to you."
        sdk = FakeSDK([
            _resp([_tool_use("t1", "get_player_stats", {}), _tool_use("t2", "get_schedule_outlook", {})], stop="tool_use"),
            _resp([_text("I'd hold McMillan.")]),
            _resp([_text(full)]),
        ])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "sell or hold?"}], tools=self._reg())
        self.assertEqual(result.text, full)
        self.assertEqual(sdk.calls[-1].get("tool_choice"), {"type": "none"})

    def test_short_answer_with_only_a_log_is_left_alone(self):
        sdk = FakeSDK([
            _resp([_text("Start Young."), _tool_use("t1", "log_prediction", {})], stop="tool_use"),
            _resp([_text("Logged.")]),
        ])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}], tools=self._reg())
        self.assertEqual(len(sdk.calls), 2)
        self.assertIn("Start Young.", result.text)


class HoldCallTests(unittest.TestCase):
    def test_hold_logged_as_a_self_trade_is_refused(self):
        ledger = Ledger(None, league_id="L", user_id="u", league_team_id="t1", season=2026, week=8,
                        resolve_player=lambda name: {"full_name": name, "sleeper_id": "9", "position": "WR"})
        out = ledger.log_prediction("trade", ["Tetairoa McMillan"], ["Tetairoa McMillan"], verdict="decline", rationale="hold", factors={}, confidence=0.6)
        self.assertIn("hold", out["error"])


if __name__ == "__main__":
    unittest.main()
