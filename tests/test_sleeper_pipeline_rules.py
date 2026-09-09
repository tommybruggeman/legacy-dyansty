"""League money rules applied to Sleeper-sourced transactions."""

import unittest
from decimal import Decimal

from services.sleeper_pipeline import (
    build_roster_map,
    dead_cap_schedule,
    faab_cap_adjustments,
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


class FaabDirectionTests(unittest.TestCase):
    def test_sender_loses_cap_and_receiver_gains_it(self):
        rows = faab_cap_adjustments([FaabMove(3, 5, 10)], {3: "team-c", 5: "team-e"})
        by_team = {row["league_team_id"]: row["amount"] for row in rows}
        # Negative is relief in cap_adjustments, so the sender takes the charge.
        self.assertEqual(by_team["team-c"], Decimal("10.00"))
        self.assertEqual(by_team["team-e"], Decimal("-10.00"))

    def test_unmapped_roster_produces_no_adjustment(self):
        self.assertEqual(faab_cap_adjustments([FaabMove(3, 99, 10)], {3: "team-c"}), ())


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
