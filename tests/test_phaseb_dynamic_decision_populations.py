from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from season_engine.commissioner_review import CommissionerPopulationBuilder
from season_engine.rollover_window import OwnerPopulationBuilder

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "supabase/migrations/20261009_dynamic_rollover_decision_populations.sql").read_text()
RECOVERY = (ROOT / "supabase/tests/recover_20261009_partial_disposable_state.sql").read_text()
VERIFY = (ROOT / "supabase/verification/verify_phaseb_dynamic_decision_populations.sql").read_text()


def exception(index, classification):
    active = classification.startswith("ACTIVE")
    return SimpleNamespace(
        classification=classification, agreement_id=f"a-{index}", player_id=f"p-{index}",
        player_name=f"Player {index}", team_id=f"t-{index % 17}",
        contract_status="active" if active else "expired",
        roster_status="unrostered" if "UNROSTERED" in classification or active else "rostered",
        taxi_or_ir=None, evidence={"salary": "3.00", "years_remaining": 1},
    )


class DynamicOwnerPopulationTests(unittest.TestCase):
    def test_zero_one_108_and_larger_are_diagnostics_not_authority(self):
        builder = OwnerPopulationBuilder()
        for size in (0, 1, 108, 257):
            report = SimpleNamespace(roster_exceptions=tuple(
                exception(i, "ROSTERED_EXPIRED_POLICY_UNDEFINED") for i in range(size)))
            result = builder.build("league", 2025, 2026, report)
            self.assertEqual((result.actual_count, result.expected_count, result.count_difference), (size, size, 0))
            self.assertFalse(any("population_count" in value for value in result.blockers))

    def test_duplicate_owner_identity_fails_closed(self):
        case = exception(1, "ROSTERED_EXPIRED_POLICY_UNDEFINED")
        result = OwnerPopulationBuilder().build("league", 2025, 2026, SimpleNamespace(roster_exceptions=(case, case)))
        self.assertIn("duplicate_owner_case:a-1", result.blockers)


class DynamicCommissionerPopulationTests(unittest.TestCase):
    def test_zero_one_13_and_larger_are_supported(self):
        for size in (0, 1, 13, 211):
            report = SimpleNamespace(roster_exceptions=tuple(
                exception(i, "ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED" if i % 2 else "EXPIRED_UNROSTERED_PUBLICATION_PENDING")
                for i in range(size)))
            with patch("season_engine.commissioner_review.RolloverAuthorityService") as service:
                service.return_value.build_rollover_readiness_report.return_value = report
                result = CommissionerPopulationBuilder().build(object(), "league", 2025, 2026)
            self.assertEqual((result.actual_count, result.expected_count, result.difference), (size, size, 0))
            self.assertFalse(any("population_count" in value for value in result.blockers))

    def test_distinct_conflict_source_identity_does_not_collapse(self):
        conflict = {"player_id":"p","player_name":"P","league_team_id":"t","agreement_id":"a",
                    "review_type":"identity_conflict","evidence":{}}
        with patch("season_engine.commissioner_review.RolloverAuthorityService") as service:
            service.return_value.build_rollover_readiness_report.return_value = SimpleNamespace(roster_exceptions=())
            result = CommissionerPopulationBuilder().build(object(), "league", 2025, 2026, conflicts=(
                {**conflict, "contract_event_id":"event-1"}, {**conflict, "contract_event_id":"event-2"}))
        self.assertEqual(result.actual_count, 2)
        self.assertFalse(result.blockers)


class PhaseBMigrationTests(unittest.TestCase):
    def test_independent_exact_set_derivation_and_failure_markers(self):
        for marker in ("phaseb_owner_expected_cases_private", "phaseb_commissioner_expected_cases_private",
                       "phaseb_assert_population_private", "phaseb_duplicate_%_case",
                       "phaseb_%_population_set_mismatch", "owner_expected_set_fingerprint",
                       "commissioner_expected_set_fingerprint"):
            self.assertIn(marker, MIGRATION)
        self.assertNotIn("108", MIGRATION)
        self.assertNotIn("expected:13", MIGRATION)

    def test_private_security_and_no_migration_time_data_writes(self):
        self.assertGreaterEqual(MIGRATION.count("security definer set search_path=pg_catalog,public"), 5)
        self.assertIn("revoke all on function public.phaseb_owner_expected_cases_private", MIGRATION)
        prefix = MIGRATION.split("alter function public.open_rollover_notice_window", 1)[0].lower()
        self.assertNotIn("insert into public.", prefix)
        self.assertNotIn("update public.", prefix)

    def test_migration_is_explicitly_atomic_and_catalog_arrays_are_typed(self):
        normalized = MIGRATION.strip().lower()
        self.assertTrue(normalized.startswith("--"))
        self.assertIn("\nbegin;", normalized)
        self.assertTrue(normalized.endswith("commit;"))
        self.assertIn("array_agg(a.attname::text", MIGRATION)
        self.assertIn("array['rollover_execution_id','player_id','review_type']::text[]", MIGRATION)
        self.assertNotIn("array_agg(a.attname order", MIGRATION)

    def test_partial_state_recovery_is_sentinel_guarded_atomic_and_narrow(self):
        self.assertIn("rollover-cardinality-certification", RECOVERY)
        self.assertIn("environment_type='disposable_test'", RECOVERY)
        self.assertIn("parent_project='Legacy-Dynasty'", RECOVERY)
        self.assertIn("phaseb_recovery_functions_present", RECOVERY)
        self.assertIn("phaseb_recovery_old_uniqueness_not_exactly_one", RECOVERY)
        self.assertIn("drop column phaseb_case_key", RECOVERY)
        self.assertNotIn("delete from", RECOVERY.lower())
        self.assertNotIn("truncate", RECOVERY.lower())
        self.assertTrue(RECOVERY.strip().lower().startswith("--"))
        self.assertTrue(RECOVERY.strip().lower().endswith("commit;"))

    def test_verification_uses_compatible_catalog_types_and_checks_uniqueness(self):
        self.assertIn("a.attname::text", VERIFY)
        self.assertIn("::text[]", VERIFY)
        self.assertIn("c.contype='u'::\"char\"", VERIFY)
        self.assertIn("obsolete collapsing uniqueness remains", VERIFY)


if __name__ == "__main__":
    unittest.main()
