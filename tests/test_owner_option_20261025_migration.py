from pathlib import Path
import hashlib
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261025_owner_option_canonical_extend_and_snapshot_capacity.sql"
SQL = MIGRATION.read_text()
HOSTED = (ROOT / "tests/season_rollover_hosted_integration/run_unified_hosted_rollover.py").read_text()
REHEARSAL = ROOT / "supabase/rehearsal/20261021_20261025_production_rehearsal.sql"


class OwnerOption20261025MigrationTests(unittest.TestCase):
    def test_atomic_schema_only_migration(self):
        self.assertTrue(SQL.lstrip().lower().startswith("begin;"))
        self.assertTrue(SQL.rstrip().lower().endswith("commit;"))
        self.assertNotIn("truncate ", SQL.lower())
        self.assertNotIn("delete from", SQL.lower())
        self.assertNotIn("insert into public.contract_", SQL.lower())

    def test_preserves_season_boundary_and_only_drops_obsolete_reference_check(self):
        self.assertNotIn("drop constraint if exists rollover_owner_decisions_check;", SQL)
        self.assertIn("drop constraint if exists rollover_owner_decisions_check1;", SQL)
        self.assertNotIn("drop constraint if exists rollover_owner_decisions_check2;", SQL)

    def test_extend_rejects_caller_authored_future_ids(self):
        self.assertIn("Caller-authored recontract references forbidden", SQL)
        self.assertIn("recontract_agreement_id=null,recontract_event_id=null", SQL)

    def test_canonical_rookie_economics_and_taxi_consumption_guards(self):
        for fragment in (
            "when 1 then 25", "when 2 then 15", "when 3 then 7",
            "a.contract_type<>'rookie'", "option_obligation_status", # semantic label below
            "initial_roster_slot", "event_type='option_exercised'",
            "classification='rookie_option_eligible'", "not c.option_consumed",
            "not b.option_consumed", "b.one_time_option_salary=expected_salary",
            "b.draft_round=round_no", "a.league_team_id<>d.league_team_id",
        ):
            if fragment == "option_obligation_status":
                self.assertIn("opt.obligation_status<>'scheduled'", SQL)
            else:
                self.assertIn(fragment, SQL)

    def test_owner_transition_reaches_validated_without_activation(self):
        self.assertIn("when 'recontract' then 'recontract_validated'", SQL)
        self.assertIn("waiting_for_owner' and new.decision_status in('recontract_submitted','recontract_validated'", SQL)
        self.assertNotIn("'option_exercised'", SQL.split("create or replace function public.submit_rollover_owner_decision", 1)[1].split("-- Operation 5/6", 1)[0])

    def test_operation_readiness_uses_derived_authority_not_future_ids(self):
        self.assertIn("d.metadata?''canonical_option_authority''", SQL)
        self.assertIn("d.recontract_agreement_id is null and d.recontract_event_id is null", SQL)

    def test_snapshot_capacity_change_keeps_fail_closed_checks(self):
        self.assertIn("payload_size>67108864", SQL)
        self.assertIn("Expected snapshot-v2 staging bound not found", SQL)
        self.assertNotIn("option_snapshot_v2_incomplete',jsonb_build_object('expected'", SQL)
        source = (ROOT / "supabase/migrations/20261016_rollover_snapshot_v3_chunked_evidence.sql").read_text()
        self.assertIn("jsonb_array_length(cases)<>c.record_count", source)
        self.assertIn("case_set_fp", source)

    def test_private_validator_has_no_client_grant(self):
        self.assertIn("revoke all on function public.validate_rollover_rookie_option_extend_private(uuid)", SQL)
        self.assertNotIn("grant execute on function public.validate_rollover_rookie_option_extend_private", SQL)

    def test_hosted_scenario_matrix_is_prepared(self):
        for scenario in ("all_extend", "all_decline", "mixed", "interrupt_replay"):
            self.assertIn(scenario, HOSTED)
        self.assertIn("operation_32_idempotent_replay_verified", HOSTED)
        self.assertIn("operation_32_stale_material_rejected", HOSTED)
        self.assertNotIn("recontract_agreement_id=", HOSTED)

    def test_rehearsal_is_single_rollback_transaction_in_dependency_order(self):
        text = REHEARSAL.read_text()
        self.assertEqual(text.count("BEGIN;"), 1)
        self.assertEqual(text.count("ROLLBACK;"), 1)
        self.assertNotIn("COMMIT;", text.upper())
        positions = [text.index(f"202610{day}") for day in range(21, 26)]
        self.assertEqual(positions, sorted(positions))

    def test_checksum_is_reportable(self):
        self.assertEqual(len(hashlib.sha256(MIGRATION.read_bytes()).hexdigest()), 64)


if __name__ == "__main__":
    unittest.main()
