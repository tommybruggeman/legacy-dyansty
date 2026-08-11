from __future__ import annotations

import unittest

from tests.fixtures.season_rollover_domain_factory import (
    BOOTSTRAP_TABLE_ALLOWLIST,
    LIFECYCLE_TABLE_DENYLIST,
    SeasonRolloverDomainFactory,
)


class SeasonRolloverDomainFactoryTests(unittest.TestCase):
    def test_domain_population_is_canonical_and_deterministic(self):
        first = SeasonRolloverDomainFactory("unit")
        second = SeasonRolloverDomainFactory("unit")
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(len(first.identity.team_ids), 10)
        self.assertEqual(len(first.identity.owner_player_ids), 108)
        self.assertEqual(len(first.identity.commissioner_player_ids), 13)
        source = first.history_source()
        self.assertEqual(len(source["rosters"]), 10)
        self.assertEqual(sum(len(row["players"]) for row in source["rosters"]), 108)
        sql = first.bootstrap_sql()
        self.assertIn(
            "rookie_class_year,draft_year,draft_round,is_rookie_contract",
            sql,
        )

    def test_bootstrap_is_allowlisted_and_contains_no_lifecycle_write(self):
        factory = SeasonRolloverDomainFactory("audit")
        writes = set(factory.audit_bootstrap_sql(factory.bootstrap_sql()))
        self.assertTrue(writes)
        self.assertTrue(writes.issubset(BOOTSTRAP_TABLE_ALLOWLIST))
        self.assertFalse(writes.intersection(LIFECYCLE_TABLE_DENYLIST))

    def test_auditor_rejects_lifecycle_insert(self):
        with self.assertRaises(AssertionError):
            SeasonRolloverDomainFactory.audit_bootstrap_sql(
                "insert into public.rollover_executions(id) values ('x')"
            )


if __name__ == "__main__":
    unittest.main()
