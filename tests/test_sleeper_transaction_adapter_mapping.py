"""Mapping rules for Sleeper transactions, checked against recorded payloads.

The three waiver fixtures are verbatim transactions from league
1361083066197491712, week 1 of the 2026 season.
"""

import unittest

from services.sleeper_transaction_adapter import (
    AcquireIntent,
    FaabMove,
    MINIMUM_ACQUISITION_SALARY,
    PickMove,
    ReleaseIntent,
    SkippedTransaction,
    TradeIntent,
    acquisition_salary,
    map_transaction,
    map_transactions,
    waiver_bid,
)

ZERO_BID_SWAP = {
    "status": "complete", "type": "waiver",
    "metadata": {"notes": "Your waiver claim was processed successfully!"},
    "created": 1788928729867, "settings": {"seq": 9, "waiver_bid": 0}, "leg": 1,
    "draft_picks": [], "creator": "1136474182021521408",
    "transaction_id": "1403272437180678144",
    "adds": {"5850": 6}, "drops": {"13319": 6},
    "consenter_ids": [6], "roster_ids": [6],
    "status_updated": 1788937690902, "waiver_budget": [],
}

PAID_BID_NULL_DROPS = {
    "status": "complete", "type": "waiver", "metadata": {},
    "created": 1788880611133, "settings": {"seq": 1, "waiver_bid": 2}, "leg": 1,
    "draft_picks": [], "creator": "741370077013807104",
    "transaction_id": "1403070612582211584",
    "adds": {"11646": 3}, "drops": None,
    "consenter_ids": [3], "roster_ids": [3],
    "status_updated": 1788937690902, "waiver_budget": [],
}

FREE_AGENT_ADD = {
    "status": "complete", "type": "free_agent", "metadata": {},
    "created": 1788881111111, "settings": None, "leg": 1,
    "draft_picks": [], "transaction_id": "1403070612582211999",
    "adds": {"4034": 2}, "drops": None,
    "roster_ids": [2], "waiver_budget": [],
}

FOUR_TEAM_TRADE = {
    "status": "complete", "type": "trade", "metadata": None,
    "created": 1788890000000, "settings": None, "leg": 1,
    "transaction_id": "1403099999999999999",
    "adds": {"1001": 2, "1002": 3, "1003": 4, "1004": 5},
    "drops": {"1001": 3, "1002": 2, "1003": 5, "1004": 4},
    "roster_ids": [2, 3, 4, 5],
    "draft_picks": [
        {"season": "2027", "round": 1, "roster_id": 4,
         "previous_owner_id": 4, "owner_id": 2, "league_id": "1361083066197491712"},
    ],
    "waiver_budget": [{"sender": 3, "receiver": 5, "amount": 10}],
}


class AcquisitionSalaryTests(unittest.TestCase):
    def test_zero_bid_becomes_one_dollar(self):
        self.assertEqual(waiver_bid(ZERO_BID_SWAP), 0)
        self.assertEqual(acquisition_salary(ZERO_BID_SWAP), MINIMUM_ACQUISITION_SALARY)

    def test_paid_bid_is_the_salary(self):
        self.assertEqual(acquisition_salary(PAID_BID_NULL_DROPS), 2)

    def test_free_agent_add_has_no_settings_and_costs_the_minimum(self):
        self.assertIsNone(waiver_bid(FREE_AGENT_ADD))
        self.assertEqual(acquisition_salary(FREE_AGENT_ADD), MINIMUM_ACQUISITION_SALARY)


class WaiverMappingTests(unittest.TestCase):
    def test_swap_releases_before_it_acquires(self):
        intents = map_transaction(ZERO_BID_SWAP)
        self.assertIsInstance(intents[0], ReleaseIntent)
        self.assertIsInstance(intents[1], AcquireIntent)
        self.assertEqual(intents[0].player_id, "13319")
        self.assertEqual(intents[1].player_id, "5850")

    def test_acquisition_is_a_one_year_deal(self):
        acquire = map_transaction(PAID_BID_NULL_DROPS)[0]
        self.assertEqual(acquire.years, 1)
        self.assertEqual(acquire.salary, 2)
        self.assertEqual(acquire.acquisition_type, "sleeper_waiver")

    def test_null_drops_do_not_crash(self):
        intents = map_transaction(PAID_BID_NULL_DROPS)
        self.assertEqual(len(intents), 1)
        self.assertIsInstance(intents[0], AcquireIntent)

    def test_idempotency_keys_are_stable_and_distinct(self):
        first = map_transaction(ZERO_BID_SWAP)
        second = map_transaction(ZERO_BID_SWAP)
        self.assertEqual([i.idempotency_key for i in first],
                         [i.idempotency_key for i in second])
        self.assertEqual(len({i.idempotency_key for i in first}), 2)


class TradeMappingTests(unittest.TestCase):
    def test_four_team_trade_pairs_every_player(self):
        trade = map_transaction(FOUR_TEAM_TRADE)[0]
        self.assertIsInstance(trade, TradeIntent)
        self.assertEqual(trade.roster_ids, (2, 3, 4, 5))
        self.assertEqual(len(trade.player_moves), 4)
        moved = {m.player_id: (m.from_roster_id, m.to_roster_id) for m in trade.player_moves}
        self.assertEqual(moved["1001"], (3, 2))
        self.assertEqual(moved["1003"], (5, 4))

    def test_draft_pick_keeps_its_traded_season(self):
        trade = map_transaction(FOUR_TEAM_TRADE)[0]
        self.assertEqual(trade.pick_moves, (PickMove(2027, 1, 4, 4, 2),))

    def test_traded_faab_is_captured_with_direction(self):
        trade = map_transaction(FOUR_TEAM_TRADE)[0]
        self.assertEqual(trade.faab_moves, (FaabMove(3, 5, 10),))


class SkipTests(unittest.TestCase):
    def test_failed_waiver_is_skipped(self):
        failed = dict(ZERO_BID_SWAP, status="failed")
        skipped = map_transaction(failed)[0]
        self.assertIsInstance(skipped, SkippedTransaction)
        self.assertIn("not complete", skipped.reason)

    def test_transaction_without_movement_is_skipped(self):
        empty = dict(ZERO_BID_SWAP, adds=None, drops=None)
        self.assertIsInstance(map_transaction(empty)[0], SkippedTransaction)


class OrderingTests(unittest.TestCase):
    def test_week_is_replayed_oldest_first(self):
        intents = map_transactions([ZERO_BID_SWAP, PAID_BID_NULL_DROPS])
        self.assertEqual(intents[0].transaction_id, PAID_BID_NULL_DROPS["transaction_id"])


if __name__ == "__main__":
    unittest.main()
