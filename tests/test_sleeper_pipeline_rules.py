"""League money rules applied to Sleeper-sourced transactions."""

import unittest
from decimal import Decimal

from services.sleeper_pipeline import (
    build_roster_map,
    dead_cap_schedule,
    describe_faab_moves,
    is_duplicate_acquisition,
    total_dead_cap,
    transactions_after_watermark,
)
from services.sleeper_transaction_adapter import FaabMove

FIFTY = Decimal("50")


def season(year, cap_hit, status="active"):
    return {"season": year, "cap_hit": cap_hit, "obligation_status": status}


class DeadCapTests(unittest.TestCase):
    def test_multi_year_deal_charges_every_remaining_season(self):
        charges = dead_cap_schedule(
            [season(2026, 20), season(2027, 20), season(2028, 20)],
            from_season=2026, dead_cap_pct=FIFTY,
        )
        self.assertEqual([c.season for c in charges], [2026, 2027, 2028])
        self.assertEqual([c.amount for c in charges],
                         [Decimal("10.00")] * 3)
        self.assertEqual(total_dead_cap(charges), Decimal("30.00"))

    def test_seasons_already_past_are_not_charged(self):
        charges = dead_cap_schedule(
            [season(2025, 20), season(2026, 20)],
            from_season=2026, dead_cap_pct=FIFTY,
        )
        self.assertEqual([c.season for c in charges], [2026])

    def test_dollar_one_contract_is_free_to_drop(self):
        charges = dead_cap_schedule(
            [season(2026, 1)], from_season=2026, dead_cap_pct=FIFTY,
        )
        self.assertEqual(total_dead_cap(charges), Decimal("0.00"))

    def test_every_faab_pickup_at_minimum_is_free_to_drop(self):
        # A $0 waiver win becomes a $1 contract, so dropping it costs nothing.
        charges = dead_cap_schedule(
            [season(2026, 1), season(2027, 1)],
            from_season=2026, dead_cap_pct=FIFTY,
        )
        self.assertEqual(total_dead_cap(charges), Decimal("0.00"))

    def test_two_dollar_contract_does_incur_a_penalty(self):
        charges = dead_cap_schedule(
            [season(2026, 2)], from_season=2026, dead_cap_pct=FIFTY,
        )
        self.assertEqual(total_dead_cap(charges), Decimal("1.00"))

    def test_released_seasons_are_ignored(self):
        charges = dead_cap_schedule(
            [season(2026, 20), season(2027, 20, "released")],
            from_season=2026, dead_cap_pct=FIFTY,
        )
        self.assertEqual([c.season for c in charges], [2026])

    def test_rounding_is_half_up_to_the_cent(self):
        charges = dead_cap_schedule(
            [season(2026, "3.33")], from_season=2026, dead_cap_pct=FIFTY,
        )
        self.assertEqual(charges[0].amount, Decimal("1.67"))

    def test_out_of_range_percentage_is_rejected(self):
        with self.assertRaises(ValueError):
            dead_cap_schedule([season(2026, 10)], from_season=2026, dead_cap_pct=101)


class RosterMapTests(unittest.TestCase):
    def test_maps_roster_id_to_team_id(self):
        mapping = build_roster_map([
            {"id": "team-a", "sleeper_roster_id": 6},
            {"id": "team-b", "sleeper_roster_id": 3},
        ])
        self.assertEqual(mapping, {6: "team-a", 3: "team-b"})

    def test_team_without_a_sleeper_roster_is_omitted(self):
        self.assertEqual(build_roster_map([{"id": "team-a", "sleeper_roster_id": None}]), {})


class FaabReportingTests(unittest.TestCase):
    def test_traded_faab_is_described_for_manual_entry(self):
        note = describe_faab_moves(
            [FaabMove(3, 5, 10)],
            {"team-c": "Chase Seyforth", "team-e": "Nando Munoz"},
            {3: "team-c", 5: "team-e"},
        )
        self.assertEqual(note, "$10 from Chase Seyforth to Nando Munoz")

    def test_unknown_roster_still_produces_a_readable_note(self):
        note = describe_faab_moves([FaabMove(3, 99, 10)], {}, {3: "team-c"})
        self.assertIn("$10", note)


class WatermarkTests(unittest.TestCase):
    def test_only_newer_transactions_are_returned_oldest_first(self):
        rows = [
            {"created": 300, "transaction_id": "c"},
            {"created": 100, "transaction_id": "a"},
            {"created": 200, "transaction_id": "b"},
        ]
        fresh = transactions_after_watermark(rows, 100)
        self.assertEqual([r["transaction_id"] for r in fresh], ["b", "c"])

    def test_a_pause_window_is_skipped_not_replayed(self):
        # Resuming sets the watermark to now; everything during the pause is gone.
        during_pause = [{"created": 500, "transaction_id": "draft-add"}]
        self.assertEqual(transactions_after_watermark(during_pause, 900), ())


class DuplicateGuardTests(unittest.TestCase):
    def test_add_for_a_player_the_team_already_owns_is_a_duplicate(self):
        agreements = [{"league_team_id": "team-a"}]
        self.assertTrue(is_duplicate_acquisition(agreements, "team-a"))

    def test_add_for_a_player_owned_elsewhere_is_not_a_duplicate(self):
        agreements = [{"league_team_id": "team-b"}]
        self.assertFalse(is_duplicate_acquisition(agreements, "team-a"))

    def test_free_agent_with_no_live_contract_is_not_a_duplicate(self):
        self.assertFalse(is_duplicate_acquisition([], "team-a"))


if __name__ == "__main__":
    unittest.main()


class TradeResolutionTests(unittest.TestCase):
    """Draft pick identity is league + year + round + ORIGINAL team."""

    def test_pick_identity_uses_original_owner_not_seller(self):
        from services.sleeper_transaction_adapter import PickMove, map_transaction

        # Roster 4 originally owned the pick and is also selling it here.
        trade = map_transaction({
            "status": "complete", "type": "trade", "created": 1,
            "transaction_id": "t1", "adds": None, "drops": None,
            "roster_ids": [2, 4],
            "draft_picks": [{"season": "2027", "round": 2,
                             "roster_id": 4, "previous_owner_id": 4, "owner_id": 2}],
            "waiver_budget": [],
        })[0]
        pick = trade.pick_moves[0]
        self.assertEqual(pick.original_roster_id, 4)
        self.assertEqual(pick.from_roster_id, 4)
        self.assertEqual(pick.to_roster_id, 2)

    def test_previously_traded_pick_keeps_its_original_owner(self):
        from services.sleeper_transaction_adapter import map_transaction

        # Roster 4 originally owned it; roster 3 acquired it earlier and now
        # flips it to roster 2. Identity must still resolve against roster 4.
        trade = map_transaction({
            "status": "complete", "type": "trade", "created": 1,
            "transaction_id": "t2", "adds": None, "drops": None,
            "roster_ids": [2, 3],
            "draft_picks": [{"season": "2028", "round": 1,
                             "roster_id": 4, "previous_owner_id": 3, "owner_id": 2}],
            "waiver_budget": [],
        })[0]
        pick = trade.pick_moves[0]
        self.assertEqual(pick.original_roster_id, 4)
        self.assertEqual(pick.from_roster_id, 3)
        self.assertEqual(pick.to_roster_id, 2)

    def test_trade_participants_stay_within_the_engine_limit(self):
        from services.sleeper_transaction_adapter import map_transaction

        trade = map_transaction({
            "status": "complete", "type": "trade", "created": 1,
            "transaction_id": "t3", "adds": {"1": 2}, "drops": {"1": 3},
            "roster_ids": [2, 3, 4, 5], "draft_picks": [], "waiver_budget": [],
        })[0]
        # execute_canonical_trade accepts 2-4 teams; the league allows up to 4.
        self.assertLessEqual(len(trade.roster_ids), 4)
        self.assertGreaterEqual(len(trade.roster_ids), 2)
