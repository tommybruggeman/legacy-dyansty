from copy import deepcopy
import unittest

from season_engine.commissioner_policy_draft import CommissionerPolicyDraftService,RELEASE_TO_HOLD,SEVEN_DAY_NOTICE_RULE
from season_engine.rollover_service import RolloverAuthorityService
from tests.test_rollover_authority import rollover_client


class CommissionerPolicyDraftTests(unittest.TestCase):
    def setUp(self):self.service=CommissionerPolicyDraftService()
    def test_draft_is_deterministic_scoped_versioned_and_no_writes(self):
        a=self.service.prepare("l1");b=self.service.prepare("l1")
        self.assertEqual(a.fingerprint,b.fingerprint);self.assertEqual((a.payload["league_id"],a.payload["source_season"],a.payload["target_season"],a.payload["version"]),("l1",2025,2026,1));self.assertEqual(a.writes_performed,0)
    def test_missing_deadline_and_failure_outcome_remain_explicit(self):
        draft=self.service.prepare("l1");self.assertFalse(draft.complete);self.assertEqual(set(draft.missing_inputs),{"owner_option_deadline","failure_to_act_outcome"});self.assertEqual(self.service.readiness(draft)["status"],"policy_input_required")
    def test_complete_draft_advances_only_to_approval(self):
        draft=self.service.prepare("l1",deadline=SEVEN_DAY_NOTICE_RULE,failure_to_act_outcome=RELEASE_TO_HOLD)
        self.assertTrue(draft.complete);self.assertEqual(self.service.readiness(draft)["status"],"commissioner_approval_required");self.assertEqual(draft.payload["status"],"draft")
        self.assertEqual(self.service.approval_packet(draft,{"starting_decisions":121}).approval_status,"approvable_unapproved")
        self.assertIsNone(draft.payload["approved_at"]);self.assertIsNone(draft.payload["effective_at"])
    def test_final_policy_keeps_release_and_publication_separate(self):
        draft=self.service.prepare("l1",deadline=SEVEN_DAY_NOTICE_RULE,failure_to_act_outcome=RELEASE_TO_HOLD);meta=draft.payload["metadata"]
        self.assertEqual(meta["post_release_state"],"commissioner_hold");self.assertEqual(len(meta["publication_requirements"]),5);self.assertIn("only inside",meta["missed_deadline_execution"])
    def test_invalid_selected_values_are_not_approvable(self):
        draft=self.service.prepare("l1",deadline="seven-ish",failure_to_act_outcome="publish")
        self.assertFalse(draft.complete);self.assertEqual(len(draft.validation_errors),2)
    def test_taxi_ir_publication_dead_cap_and_overlap_are_explicit(self):
        payload=self.service.prepare("l1").payload;meta=payload["metadata"]
        self.assertIn("taxi",payload["taxi_policy"]);self.assertIn("IR",payload["ir_policy"]);self.assertEqual(meta["overlapping_active_agreement_policy"],"blocked")
        self.assertIn("commissioner_hold",payload["free_agent_publication_policy"]);self.assertIn("qualifying_early_termination",payload["dead_cap_policy"])
    def test_four_scenarios_account_for_decisions_and_separate_publication(self):
        by={x.scenario_id:x for x in self.service.scenarios()};self.assertEqual(set(by),{"A","B","C","D"})
        self.assertEqual(by["A"].owner_decisions,108);self.assertEqual(by["A"].policy_resolved,108);self.assertEqual(by["A"].blockers,("commissioner approval required",));self.assertEqual(by["B"].policy_resolved,108);self.assertEqual(by["C"].commissioner_decisions,121);self.assertEqual(by["D"].policy_resolved,108)
        self.assertGreaterEqual(by["B"].publication_candidates,11);self.assertIn("natural expiration creates no dead cap",("publication remains a later explicit action","natural expiration creates no dead cap"))
    def test_incomplete_draft_does_not_falsely_resolve_108(self):
        client=rollover_client();before=deepcopy(client.rows);exceptions=RolloverAuthorityService(client).build_rollover_readiness_report("l1").roster_exceptions
        impact=self.service.reduce(self.service.prepare("l1"),exceptions);self.assertEqual(impact["policy_resolved"],0);self.assertEqual(impact["blocked_by_missing_input"],1);self.assertEqual(client.rows,before)
    def test_complete_draft_preserves_off_roster_and_publication_review(self):
        exceptions=RolloverAuthorityService(rollover_client()).build_rollover_readiness_report("l1").roster_exceptions
        impact=self.service.reduce(self.service.prepare("l1",deadline=SEVEN_DAY_NOTICE_RULE,failure_to_act_outcome=RELEASE_TO_HOLD),exceptions)
        self.assertEqual(impact["policy_resolved"],1);self.assertEqual(impact["commissioner_actions_required"],1)
    def test_approval_packet_is_unapproved_and_has_no_execution_command(self):
        draft=self.service.prepare("l1");impact={"starting_decisions":121};packet=self.service.approval_packet(draft,impact)
        self.assertEqual(packet.approval_status,"not_approvable");self.assertIsNone(packet.execution_command);self.assertTrue(packet.unresolved_required_choices);self.assertIn("Approval does not execute rollover.",packet.warnings)

if __name__=="__main__":unittest.main()
