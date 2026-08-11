from pathlib import Path
import unittest

from services.season_rollover_control import SeasonRolloverControlService, derive_lifecycle_timeline


ROOT = Path(__file__).parents[1]
UI = (ROOT / "services/season_rollover_ui.py").read_text()
SQL = (ROOT / "supabase/migrations/20260924_rollover_disposable_clock_seam.sql").read_text()


class HostedReviewTests(unittest.TestCase):
    def test_outcomes_come_from_existing_matrix(self):
        outcomes = SeasonRolloverControlService.allowed_review_outcomes({"review_type": "active_off_roster_liability"})
        self.assertIn("preserve_active_liability", outcomes)
        self.assertNotIn("approve_publication", outcomes)

    def test_timeline_represents_review_and_authority_progress(self):
        state = {"execution": {"status": "decision_window_closed"}, "commissioner_reviews": [
            {"id": "r1", "review_state": "approved"}, {"id": "r2", "review_state": "under_review"}],
            "preparations": [], "simulations": [], "plans": [], "approvals": [], "operation_results": [],
            "finalizations": []}
        stage = derive_lifecycle_timeline(state)[4]
        self.assertEqual(stage.status, "current")
        self.assertIn("1/2 reviews", stage.summary)

    def test_ui_requires_review_and_prepare_confirmations(self):
        self.assertIn('f"REVIEW {safe_id}"', UI)
        self.assertIn('f"PREPARE {target}"', UI)
        self.assertIn("initialize_canonical_reviews", UI)
        self.assertIn("prepare_canonical_authorities", UI)

    def test_clock_is_private_disposable_and_narrow(self):
        self.assertIn("rollover_is_approved_disposable", SQL)
        self.assertIn("public.rollover_effective_now()<x.owner_deadline", SQL)
        self.assertIn("revoke all on function public.rollover_is_approved_disposable()", SQL)
        self.assertNotIn("update public.rollover_executions set notice_timestamp", SQL)


if __name__ == "__main__": unittest.main()
