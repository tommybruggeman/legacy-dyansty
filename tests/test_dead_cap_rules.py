"""One dead cap rule, quoted identically by every path that drops a player.

The Manual Drop screen and the Sleeper sync each computed the penalty on their
own and disagreed on minimum contracts -- the same $1 drop cost $0.00 through
the sync and $0.50 through the screen. These tests pin the rule and the
agreement.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from services.dead_cap_rules import dead_cap_for_season
from services.offseason_transactions import calculate_default_dead_cap
from services.sleeper_pipeline import dead_cap_schedule

PCT = 50


class DeadCapRule(unittest.TestCase):
    def test_minimum_contracts_are_free_to_drop(self):
        for salary in ("0", "0.50", "1", "1.00"):
            with self.subTest(salary=salary):
                self.assertEqual(dead_cap_for_season(salary, PCT), Decimal("0.00"))

    def test_everything_above_the_minimum_pays_the_exact_percentage(self):
        self.assertEqual(dead_cap_for_season("2", PCT), Decimal("1.00"))
        self.assertEqual(dead_cap_for_season("3", PCT), Decimal("1.50"))
        self.assertEqual(dead_cap_for_season("15", PCT), Decimal("7.50"))

    def test_percentage_outside_zero_to_one_hundred_is_rejected(self):
        for pct in (-1, 101):
            with self.subTest(pct=pct), self.assertRaises(ValueError):
                dead_cap_for_season("10", pct)


class PathsAgree(unittest.TestCase):
    """What Manual Drop quotes is what the sync would charge, dollar for dollar."""

    def quote_from_manual_drop(self, salary):
        return calculate_default_dead_cap({"default_dead_cap_pct": PCT}, salary)

    def quote_from_sync(self, salary):
        charges = dead_cap_schedule(
            [{"season": 2026, "salary": salary, "cap_hit": salary,
              "obligation_status": "active"}],
            from_season=2026, dead_cap_pct=Decimal(str(PCT)),
        )
        return charges[0].amount

    def test_both_paths_quote_the_same_number(self):
        for salary in ("1", "2", "3", "7", "9", "15", "22"):
            with self.subTest(salary=salary):
                self.assertEqual(
                    self.quote_from_manual_drop(salary), self.quote_from_sync(salary),
                )

    def test_the_dollar_contract_that_started_this_is_free_on_both_paths(self):
        self.assertEqual(self.quote_from_manual_drop("1"), Decimal("0.00"))
        self.assertEqual(self.quote_from_sync("1"), Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
