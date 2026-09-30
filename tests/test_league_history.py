import unittest

from pipeline.league_history.build import OwnerMap, build_draft_events, build_matchups, build_transaction_events
from league_ai.tools.history import LeagueHistory, history_tools, pack_history_summary
from tests.test_league_ai_tools import FakeClient, FakeQuery

LEAGUE = "11111111-1111-1111-1111-111111111111"
USERS = [{"user_id": "u1", "display_name": "tommyb", "metadata": {"team_name": "Big Cats"}}, {"user_id": "u2", "display_name": "chase_s"}, {"user_id": "u3", "display_name": "dyl"}]
ROSTERS = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u2"}, {"roster_id": 7, "owner_id": "u3"}]
TEAMS = [{"owner_name": "Tommy Bruggeman", "sleeper_roster_id": 1, "sleeper_user_id": "u1"}, {"owner_name": "Chase Seyforth", "sleeper_roster_id": 2, "sleeper_user_id": "u2"}, {"owner_name": "Dylan Burruel", "sleeper_roster_id": 7, "sleeper_user_id": "u3"}]
PLAYERS = {"p1": {"full_name": "Josh Allen", "position": "QB"}, "p2": {"full_name": "Bijan Robinson", "position": "RB"}, "p3": {"full_name": "Waiver Guy", "position": "WR"}}
TXS = [
    {"transaction_id": "t1", "type": "trade", "status": "complete", "leg": 3, "status_updated": 1758000000000, "roster_ids": [1, 2],
     "adds": {"p1": 2, "p2": 1}, "drops": {"p1": 1, "p2": 2},
     "draft_picks": [{"season": "2027", "round": 1, "roster_id": 1, "previous_owner_id": 1, "owner_id": 2}],
     "waiver_budget": [{"sender": 2, "receiver": 1, "amount": 5}]},
    {"transaction_id": "t2", "type": "waiver", "status": "complete", "leg": 2, "status_updated": 1757000000000, "adds": {"p3": 7}, "drops": {"p2": 7}, "settings": {"waiver_bid": 12}},
    {"transaction_id": "t3", "type": "waiver", "status": "failed", "adds": {"p3": 1}},
]
MATCHUPS_W1 = [
    {"matchup_id": 1, "roster_id": 1, "points": 120.5}, {"matchup_id": 1, "roster_id": 2, "points": 99.0},
    {"matchup_id": 2, "roster_id": 7, "points": 80.0}, {"matchup_id": 2, "roster_id": 9, "points": 130.0},
]


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.owners = OwnerMap(USERS, ROSTERS, TEAMS)

    def test_owner_map_prefers_app_names(self):
        self.assertEqual(self.owners.name(1), "Tommy Bruggeman")
        self.assertEqual(self.owners.name(9), "Roster 9")

    def test_trade_legs_and_waiver(self):
        rows = build_transaction_events(LEAGUE, 2026, TXS, self.owners, PLAYERS, {"p2": {"salary": 45, "years": 3}})
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds.count("trade"), 3)  # 2 players + 1 pick
        self.assertEqual(kinds.count("waiver"), 1)
        self.assertEqual(kinds.count("drop"), 1)
        trade = next(r for r in rows if r["player_name"] == "Bijan Robinson")
        self.assertEqual(trade["owner_name"], "Tommy Bruggeman")
        self.assertEqual(trade["counterparty_names"], ["Chase Seyforth"])
        self.assertEqual(trade["contract_salary"], 45)
        self.assertIn("Josh Allen Tommy Bruggeman -> Chase Seyforth", trade["summary"])
        self.assertIn("2027 R1 pick (orig. Tommy Bruggeman) Tommy Bruggeman -> Chase Seyforth", trade["summary"])
        self.assertIn("$5 FAAB Chase Seyforth -> Tommy Bruggeman", trade["summary"])
        waiver = next(r for r in rows if r["kind"] == "waiver")
        self.assertEqual(waiver["faab_bid"], 12)
        self.assertEqual(waiver["summary"], "Dylan Burruel added Waiver Guy (WR) for $12 FAAB")
        self.assertFalse(any(r["event_id"].startswith("sleeper:t3") for r in rows))

    def test_draft_events(self):
        rows = build_draft_events(LEAGUE, 2026, {"draft_id": "d1", "type": "linear", "start_time": 1750000000000}, [
            {"player_id": "p2", "roster_id": 1, "round": 1, "draft_slot": 2, "pick_no": 2, "metadata": {"first_name": "Bijan", "last_name": "Robinson", "position": "RB"}}
        ], self.owners, PLAYERS)
        self.assertEqual(rows[0]["summary"], "Tommy Bruggeman selected Bijan Robinson (RB) at 2026 rookie draft 1.02")

    def test_matchups_with_top_bonus(self):
        rows = build_matchups(LEAGUE, 2026, 1, MATCHUPS_W1, self.owners, top_n=2)
        tommy = next(r for r in rows if r["owner_name"] == "Tommy Bruggeman")
        self.assertTrue(tommy["won"]); self.assertTrue(tommy["top_scorer_bonus"]); self.assertEqual(tommy["standing_points"], 3)
        dylan = next(r for r in rows if r["owner_name"] == "Dylan Burruel")
        self.assertFalse(dylan["won"]); self.assertEqual(dylan["standing_points"], 0)
        self.assertEqual(dylan["opponent_name"], "Roster 9")
        self.assertEqual(build_matchups(LEAGUE, 2026, 9, [{"matchup_id": 1, "roster_id": 1, "points": 0}, {"matchup_id": 1, "roster_id": 2, "points": 0}], self.owners), [])


EVENTS = [
    {"league_id": LEAGUE, "event_id": "e1", "kind": "trade", "season": 2025, "week": 5, "occurred_at": "2025-10-01", "owner_name": "Tommy Bruggeman", "counterparty_names": ["Chase Seyforth"], "player_name": "Josh Allen", "position": "QB", "summary": "Trade (Chase / Tommy): Josh Allen ..."},
    {"league_id": LEAGUE, "event_id": "e2", "kind": "trade", "season": 2025, "week": 5, "occurred_at": "2025-10-01", "owner_name": "Chase Seyforth", "counterparty_names": ["Tommy Bruggeman"], "player_name": "Bijan Robinson", "position": "RB", "summary": "Trade (Chase / Tommy): Josh Allen ..."},
    {"league_id": LEAGUE, "event_id": "e3", "kind": "waiver", "season": 2026, "week": 2, "occurred_at": "2026-09-16", "owner_name": "Dylan Burruel", "player_name": "Waiver Guy", "position": "WR", "faab_bid": 12, "summary": "Dylan added Waiver Guy for $12"},
    {"league_id": LEAGUE, "event_id": "e4", "kind": "waiver", "season": 2026, "week": 3, "occurred_at": "2026-09-23", "owner_name": "Tommy Bruggeman", "player_name": "Other Guy", "position": "RB", "faab_bid": 3, "summary": "Tommy added Other Guy for $3"},
]
MATCHUPS = [
    {"league_id": LEAGUE, "season": 2025, "week": 1, "owner_name": "Tommy Bruggeman", "opponent_name": "Chase Seyforth", "points": 120, "opponent_points": 100, "won": True, "top_scorer_bonus": True, "standing_points": 3, "is_playoff": False},
    {"league_id": LEAGUE, "season": 2025, "week": 1, "owner_name": "Chase Seyforth", "opponent_name": "Tommy Bruggeman", "points": 100, "opponent_points": 120, "won": False, "top_scorer_bonus": False, "standing_points": 0, "is_playoff": False},
    {"league_id": LEAGUE, "season": 2025, "week": 2, "owner_name": "Tommy Bruggeman", "opponent_name": "Chase Seyforth", "points": 90, "opponent_points": 110, "won": False, "top_scorer_bonus": False, "standing_points": 0, "is_playoff": False},
    {"league_id": LEAGUE, "season": 2025, "week": 2, "owner_name": "Chase Seyforth", "opponent_name": "Tommy Bruggeman", "points": 110, "opponent_points": 90, "won": True, "top_scorer_bonus": True, "standing_points": 3, "is_playoff": False},
]
FakeClient.TABLES["league_history_events"] = EVENTS
FakeClient.TABLES["league_matchups"] = MATCHUPS


class HistoryToolTests(unittest.TestCase):
    def setUp(self):
        self.h = LeagueHistory(FakeClient(), LEAGUE)

    def test_events_collapse_trade_legs(self):
        rows = self.h.events(kind="trade")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(self.h.events(owner="tommy")), 2)

    def test_waiver_prices(self):
        out = self.h.waiver_prices()
        self.assertEqual(out["biggest_bids"][0]["faab_bid"], 12)
        self.assertEqual(out["by_position"]["WR"]["max"], 12)

    def test_trade_tendencies(self):
        out = self.h.trade_partners()
        self.assertEqual(out["total_trades"], 1)
        self.assertEqual(out["most_common_pairs"], {"Chase Seyforth & Tommy Bruggeman": 1})

    def test_season_summary_and_head_to_head(self):
        s = self.h.season_summaries()["2025"]
        self.assertEqual(s[0]["owner"], "Tommy Bruggeman")  # 3 sp vs 3 sp, PF 210 vs 210 -> stable order; both tie
        h2h = self.h.head_to_head("Tommy", "Chase")
        self.assertEqual((h2h["games"], h2h["wins_for_first"], h2h["wins_for_second"]), (2, 1, 1))
        lines = pack_history_summary(FakeClient(), LEAGUE)
        self.assertTrue(lines[0].startswith("2025: top "))

    def test_tool_specs(self):
        names = [t.name for t in history_tools(FakeClient(), LEAGUE)]
        self.assertEqual(names, ["get_league_history", "get_waiver_prices", "get_trade_tendencies", "get_matchup_history"])


if __name__ == "__main__":
    unittest.main()
