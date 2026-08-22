from pathlib import Path
import re
import unittest

RUNNER = Path(__file__).parent / "phase_b_decision_population_integration/run_phase_b_decision_population_certification.py"
SOURCE = RUNNER.read_text()


class PhaseBIntegrationRunnerSafetyTests(unittest.TestCase):
    def test_every_matrix_case_uses_unique_namespace_and_transaction_rollback(self):
        self.assertIn('setup(session, f"phaseb-{kind}-{size}"', SOURCE)
        self.assertIn('session.command("begin")', SOURCE)
        self.assertGreaterEqual(SOURCE.count('session.command("rollback")'), 2)

    def test_runner_never_mutates_immutable_history(self):
        immutable = (
            "season_roster_assignments", "season_team_mappings", "season_standings",
            "season_matchups", "season_playoff_brackets", "historical_capture_executions",
        )
        for table in immutable:
            self.assertIsNone(re.search(
                rf"\b(?:delete\s+from|update)\s+(?:public\.)?{table}\b", SOURCE, re.I), table)
        self.assertIn("audit_harness_immutable_writes()", SOURCE)

    def test_required_matrix_and_identity_cases_remain_present(self):
        for marker in ('("owner", (0, 1, 108, 120))', '("commissioner", (0, 1, 13, 25))',
                       '"missing"', '"added"', '"duplicate"', '"stale"', '"cross_league"',
                       'duplicate_phaseb_case_key', 'distinct_identity', 'cross_language'):
            self.assertIn(marker, SOURCE)
        for marker in ('foreign_agreement_local_team','local_agreement_foreign_team',
                       'equal_count_foreign_substitution','foreign_agreement_team',
                       'foreign_source_identity'):
            self.assertIn(marker,SOURCE)

    def test_runner_reports_progress_before_each_matrix_cell(self):
        self.assertIn('[Phase B] {kind} positive {size}',SOURCE)
        self.assertIn('[Phase B] {kind} {name}',SOURCE)
        self.assertIn('[Phase B] commissioner distinct_identity',SOURCE)
        self.assertIn('[Phase B] commissioner duplicate_phaseb_case_key',SOURCE)


if __name__ == "__main__":
    unittest.main()
