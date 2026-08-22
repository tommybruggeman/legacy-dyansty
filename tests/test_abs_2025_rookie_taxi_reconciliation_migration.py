from pathlib import Path
import unittest


SQL = (Path(__file__).parents[1] / "supabase/migrations/20261020_abs_2025_rookie_taxi_contract_reconciliation.sql").read_text()
LOWER = SQL.lower()


class AbsReconciliationMigrationTests(unittest.TestCase):
    def test_exact_league_boundary_and_transition_are_guarded(self):
        self.assertIn("9838a0a1-97c6-4cab-bb88-af177317abfe", SQL)
        self.assertIn("cbcef849-5ba2-4b1a-88bc-b285f73b0740", SQL)
        self.assertIn("2026-07-29 17:26:27.867332+00", SQL)

    def test_legacy_fingerprints_are_guarded(self):
        self.assertIn("d852eb7df1a819ff32a468fb44c73347384d3a998d2e37a22ef446be96e894c8", SQL)
        self.assertIn("2b0df8a91fd3693fdbe7f42941ab325d8c588bcd1ac8806b4a7104c6894d2a4f", SQL)

    def test_advisory_lock_and_atomic_transaction(self):
        self.assertIn("pg_advisory_xact_lock", LOWER)
        self.assertTrue(LOWER.rstrip().endswith("commit;"))

    def test_exact_board_and_taxi_population(self):
        self.assertIn("<>30", SQL)
        self.assertIn("<>9", SQL)
        self.assertIn("('12530',1,5,6,2,25)", SQL)

    def test_exact_classification_counts(self):
        for value in ("<>74", "<>113", "<>12", "<>9", "<>3"):
            self.assertIn(value, SQL)

    def test_unexercised_options_are_scheduled(self):
        self.assertIn("'scheduled',true,'rookie_one_time_resign_option'", SQL)
        self.assertNotIn("'active',true,'rookie_one_time_resign_option'", SQL)

    def test_taxi_paused_rows_are_initial_contract_not_options(self):
        self.assertIn("'scheduled',false,null,'abs_2025_taxi_preserved_initial_contract_v1'", LOWER)

    def test_taxi_charge_is_half_and_year_not_consumed(self):
        self.assertIn("taxi_charge=round(normal_annual_charge*0.50,2)", SQL)
        self.assertIn("check(not contract_year_consumed)", LOWER)

    def test_source_and_target_authority_are_reversed_only_to_preexecution(self):
        self.assertIn("season=2025 and obligation_status='satisfied'", SQL)
        self.assertIn("season=2026 and obligation_status='active'", SQL)
        self.assertNotIn("update public.league_seasons", LOWER)

    def test_roster_ownership_is_never_mutated(self):
        self.assertNotIn("update public.season_roster_assignments", LOWER)
        self.assertNotIn("delete from public.season_roster_assignments", LOWER)

    def test_historical_transition_and_events_are_not_deleted(self):
        self.assertNotIn("delete from public.contract_transition_executions", LOWER)
        self.assertNotIn("delete from public.contract_events", LOWER)
        self.assertIn("expiration_events_preserved',119", LOWER)

    def test_no_rollover_execution_is_created(self):
        self.assertNotIn("insert into public.rollover_executions", LOWER)

    def test_no_option_is_exercised(self):
        self.assertNotIn("'option_exercised'", LOWER)

    def test_no_publication_or_season_activation(self):
        self.assertNotIn("insert into public.free_agent_publications", LOWER)
        self.assertNotIn("is_active=true", LOWER)

    def test_ordinary_expiration_has_dedicated_release_path(self):
        self.assertIn("phase3b7c-ordinary-expiration-v1", LOWER)
        self.assertIn("classification='ordinary_expiration'", LOWER)

    def test_prior_transition_filter_is_exact_and_certified(self):
        self.assertIn("r.legacy_transition_id=t.id and r.reconciliation_status='certified'", LOWER)

    def test_before_rows_are_preserved(self):
        self.assertIn("contract_reconciliation_before_rows", LOWER)
        self.assertIn("before_fingerprint", LOWER)

    def test_replay_fails_before_mutation(self):
        self.assertIn("abs_reconciliation_already_started_or_published", LOWER)
        self.assertIn("exists(select 1 from public.contract_transition_reconciliations", LOWER)

    def test_future_board_rpc_requires_canonical_team_and_player(self):
        self.assertIn("rookie board canonical identity invalid", LOWER)
        self.assertIn("persist_rookie_draft_board_authenticated", LOWER)

    def test_postcondition_requires_canonical_preflight_ready(self):
        self.assertIn("abs_reconciliation_postcondition_failed", LOWER)
        self.assertIn("(ready->>'prepared_option_count')::int<>3", LOWER)


if __name__ == "__main__":
    unittest.main()
