from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from season_engine.rollover_window import OwnerPopulationBuilder
from season_engine.dry_run_simulator import RolloverDryRunSimulator
from services.season_rollover_control import SeasonRolloverControlService, RolloverControlError


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261026_abs_2025_2026_immediate_rollover_authority.sql"
SQL = MIGRATION.read_text()
LOWER = SQL.lower()
LEAGUE = "9838a0a1-97c6-4cab-bb88-af177317abfe"


def option_case(player="12512", agreement="00000000-0000-0000-0000-000000000001"):
    return SimpleNamespace(classification="ROSTERED_EXPIRED_POLICY_UNDEFINED", agreement_id=agreement,
        player_id=player, player_name=player, team_id="00000000-0000-0000-0000-000000000002",
        contract_status="expired", roster_status="rostered", taxi_or_ir=None,
        proposed_action="HOLD_FOR_OWNER_ROOKIE_OPTION_DECISION", evidence={"salary":"1.00","years_remaining":0})


class CanonicalOwnerPopulationTests(unittest.TestCase):
    def test_python_builder_uses_classification_rows_and_supports_zero(self):
        report = SimpleNamespace(roster_exceptions=(option_case(),))
        result = OwnerPopulationBuilder().build(LEAGUE, 2025, 2026, report, [])
        self.assertEqual(result.actual_count, 0)
        self.assertFalse(result.blockers)

    def test_python_builder_includes_only_canonical_option_classification(self):
        case = option_case()
        rows = [{"league_id":LEAGUE,"source_season":2025,"target_season":2026,
                 "contract_agreement_id":case.agreement_id,"classification":"rookie_option_eligible"}]
        self.assertEqual(OwnerPopulationBuilder().build(LEAGUE,2025,2026,
            SimpleNamespace(roster_exceptions=(case,)),rows).actual_count,1)

    def test_dry_run_accepts_only_explicit_canonical_zero_owner_count(self):
        simulation = Mock(blockers=(), warnings=(), execution_id="x", source_season=2025, target_season=2026,
                          finalized_owner_outcomes=(), finalized_commissioner_outcomes=({"id":"review"},),
                          owner_expected_count=0)
        simulator = RolloverDryRunSimulator()
        with unittest.mock.patch.object(simulator, "_contracts", return_value=()), \
             unittest.mock.patch.object(simulator, "_rosters", return_value=((),(),())), \
             unittest.mock.patch.object(simulator, "_publication", return_value=()), \
             unittest.mock.patch.object(simulator, "_dead_cap", return_value=()), \
             unittest.mock.patch.object(simulator, "_teams", return_value=()), \
             unittest.mock.patch.object(simulator, "_season", return_value=()), \
             unittest.mock.patch.object(simulator, "_pages", return_value={}), \
             unittest.mock.patch.object(simulator, "_mutations", return_value=()):
            # Remaining rich authority fields are intentionally exercised by the hosted certification.
            simulation.cap_authority_plan.projected_team_cap_states = ()
            result = simulator.simulate(simulation)
        self.assertNotIn("finalized_owner_outcomes_missing", result.blockers)
        self.assertNotIn("finalized_owner_outcomes_count_mismatch", result.blockers)


class ImmediateMigrationTests(unittest.TestCase):
    def test_forward_only_atomic_migration(self):
        self.assertTrue(LOWER.lstrip().startswith("begin;"))
        self.assertTrue(LOWER.rstrip().endswith("commit;"))
        self.assertNotIn("update public.league_seasons", LOWER)
        self.assertNotIn("insert into public.rollover_executions", LOWER)

    def test_exact_before_and_after_population_guards(self):
        for fragment in ("<>211", "<>3", "array['12483','12512','12547']", "<>74", "<>116", "<>12", "<>9", "<>0"):
            self.assertIn(fragment, SQL)
        self.assertIn("classification='rookie_initial_taxi_paused'", SQL)

    def test_only_exact_three_are_reclassified_without_domain_mutation(self):
        self.assertIn("c.player_id in('12512','12483','12547')", SQL)
        self.assertIn("classification='ordinary_expiration'", SQL)
        for forbidden in ("update public.contract_agreements", "insert into public.contract_events",
                          "update public.season_roster_assignments", "delete from public.rookie_draft_board_assignments"):
            self.assertNotIn(forbidden, LOWER)

    def test_reconciliation_preserves_history_and_records_directive(self):
        self.assertIn("abs:2025:2026:legacy-transition-reconciliation:v1", SQL)
        self.assertIn("legacy_transition_preserved',true", LOWER)
        self.assertIn("commissioner_directed',true", LOWER)
        self.assertIn("'ordinary_expirations',116", LOWER)

    def test_normal_close_is_not_replaced_or_clock_changed(self):
        self.assertNotIn("create or replace function public.close_rollover_decision_window(", LOWER)
        self.assertNotIn("create or replace function public.rollover_effective_now", LOWER)
        self.assertIn("d:=n+interval '7 days'", LOWER)

    def test_immediate_close_has_every_narrow_guard(self):
        for fragment in ("close_abs_2025_2026_immediate_rollover_authenticated", LEAGUE,
            "x.source_season<>2025", "x.target_season<>2026", "x.status<>'decision_window_open'",
            "phaseb_owner_expected_cases_private", "canonical_count<>0", "actual_count<>0",
            "decision_population_fingerprint is distinct from canonical_fp", "require_commissioner_authority",
            "required_confirmation", "consumed_at is not null", "status='decision_window_closed'",
            "authorized_by=actor", "rollover_execution_id=x.id", "evidence_fingerprint"):
            self.assertIn(fragment, SQL)
        self.assertIn("current_classification_fp is distinct from authority.classification_population_fingerprint", SQL)

    def test_zero_owner_open_uses_legal_sequential_states(self):
        self.assertIn("set status='notice_open'", SQL)
        self.assertIn("set status='decision_window_open'", SQL)
        self.assertIn("d:=n+interval '7 days'", SQL)

    def test_authority_preparation_uses_canonical_zero_owner_parity(self):
        self.assertIn("owner_count<>(select count(*) from public.phaseb_owner_expected_cases_private(x.id))", SQL)
        self.assertIn("execute replace(definition,old_fragment,new_fragment)", SQL)
        self.assertIn("public.persist_rollover_dry_run_service(jsonb)", SQL)
        self.assertIn("check(case_count>=0)", SQL)

    def test_no_fake_decisions_or_no_response_rows(self):
        self.assertNotIn("insert into public.rollover_owner_decisions", LOWER)
        self.assertNotIn("values('no_response'", LOWER)

    def test_authority_replay_fails_closed(self):
        self.assertIn("authority absent or already consumed", LOWER)
        self.assertIn("immutable after consumption", LOWER)


class ImmediateControlServiceTests(unittest.TestCase):
    def service(self, league=LEAGUE, source=2025, target=2026, status="decision_window_open"):
        service = SeasonRolloverControlService(Mock(), league)
        service._scoped_execution = Mock(return_value={"id":"execution","source_season":source,
            "target_season":target,"status":status,"decision_population_fingerprint":"f"*64})
        service.authenticated_rpc = Mock(return_value={"execution":{"status":"decision_window_closed"}})
        return service

    def test_control_calls_only_dedicated_rpc_with_exact_material(self):
        service = self.service()
        service.close_abs_2025_2026_immediate_rollover("execution","CONFIRM")
        name, request = service.authenticated_rpc.call_args.args
        self.assertEqual(name,"close_abs_2025_2026_immediate_rollover_authenticated")
        self.assertEqual(request["confirmation"],"CONFIRM")
        self.assertEqual(request["expected_population_fingerprint"],"f"*64)

    def test_control_rejects_other_league_season_and_state(self):
        for service in (self.service("other"),self.service(source=2024,target=2025),self.service(status="preflight_ready")):
            with self.assertRaises(RolloverControlError):
                service.close_abs_2025_2026_immediate_rollover("execution","CONFIRM")


if __name__ == "__main__":
    unittest.main()
