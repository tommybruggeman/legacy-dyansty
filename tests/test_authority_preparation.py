from datetime import datetime, timezone
from decimal import Decimal
import unittest

from season_engine.authority_preparation import (
    AuthorityPreparationValidator, CapAuthorityPlanner, DeadCapAuthorityPlanner,
    PublicationAuthorityPlanner, authority_preparation_readiness,
    build_preparation_package, material_fingerprint, to_simulation_input,
)


class PublicationTests(unittest.TestCase):
    def setUp(self): self.planner = PublicationAuthorityPlanner()
    def one(self, **changes):
        row = dict(player_id="p1", agreement_id="a1", league_team_id="t1", source_status="expired",
                   planned_contract_outcome="release", active_agreement=False, original_team_liability=False,
                   owner_outcome_final=True, commissioner_outcome_final=True,
                   commissioner_outcome="approve_publication")
        row.update(changes); return self.planner.plan([row]).instructions[0]
    def test_active_agreement_blocks(self): self.assertIn("blocked_by_active_agreement", self.one(active_agreement=True).publication_blockers)
    def test_original_liability_blocks(self): self.assertIn("blocked_by_original_team_liability", self.one(original_team_liability=True).publication_blockers)
    def test_second_agreement_blocks(self): self.assertIn("blocked_by_second_agreement_conflict", self.one(second_agreement_conflict=True).publication_blockers)
    def test_natural_expiration_does_not_publish_without_approval(self):
        self.assertEqual(self.one(commissioner_outcome="reject_publication").publication_action, "commissioner_hold")
    def test_approval_is_only_future_instruction(self): self.assertEqual(self.one().publication_action, "plan_publication_at_execution")
    def test_retained_player_not_published(self):
        row = self.one(planned_contract_outcome="retain", active_agreement=True)
        self.assertNotEqual(row.publication_action, "plan_publication_at_execution")


class DeadCapTests(unittest.TestCase):
    def setUp(self): self.planner = DeadCapAuthorityPlanner()
    def one(self, **changes):
        row = dict(player_id="p1", agreement_id="a1", league_team_id="t1", dead_cap_requested=False,
                   termination_type="natural_expiration")
        row.update(changes); return self.planner.plan([row], 2026).instructions[0]
    def test_natural_expiration_none(self): self.assertEqual(self.one().calculated_amount, Decimal("0"))
    def test_no_response_none(self): self.assertEqual(self.one(owner_outcome="no_response").planned_action, "no_dead_cap")
    def test_decline_without_event_none(self): self.assertEqual(self.one(owner_outcome="decline").planned_action, "no_dead_cap")
    def test_qualifying_event_required(self): self.assertIn("qualifying_event_required", self.one(dead_cap_requested=True, termination_type="early").blockers)
    def test_penalty_rule_required(self):
        self.assertIn("penalty_rule_required", self.one(dead_cap_requested=True, termination_type="early", qualifying_event_id="e1", salary_basis=10).blockers)
    def test_amount_deterministic(self):
        args=dict(dead_cap_requested=True, termination_type="early", qualifying_event_id="e1", penalty_rule="half", salary_basis=7, penalty_rate="0.5")
        self.assertEqual(self.one(**args).calculated_amount, Decimal("4"))
    def test_milroe_and_harris_none(self):
        rows=[dict(player_id=x,agreement_id=x,league_team_id="t",dead_cap_requested=False) for x in ("Jalen Milroe","Tre Harris")]
        self.assertTrue(all(x.calculated_amount == 0 for x in self.planner.plan(rows,2026).instructions))


class CapTests(unittest.TestCase):
    def setUp(self): self.planner=CapAuthorityPlanner()
    def test_rookie_scaling(self):
        self.assertEqual(self.planner.scale_amount(Decimal("1"),Decimal("200"),Decimal("225")),Decimal("1"))
        self.assertEqual(self.planner.scale_amount(Decimal("7"),Decimal("200"),Decimal("225")),Decimal("8"))
    def test_nearest_dollar(self): self.assertEqual(self.planner.scale_amount(Decimal("7"),Decimal("200"),Decimal("250")),Decimal("9"))
    def test_team_projection_and_credits(self):
        plan=self.planner.plan(league_id="l",source_season=2025,target_season=2026,source_cap=200,target_cap=225,
          teams=[dict(league_team_id="t",source_salary_total=190,retained_salary_total=180,recontract_salary_total=10,
                      planned_dead_cap=2,cap_adjustments=1,cap_credits_in=5,cap_credits_out=3)])
        self.assertEqual(plan.target_cap,Decimal("225"));self.assertEqual(plan.projected_team_cap_states[0].projected_cap_charge,Decimal("191"))
    def test_unresolved_salary_does_not_overstate_legality(self):
        plan=self.planner.plan(league_id="l",source_season=2025,target_season=2026,source_cap=200,target_cap=225,
          teams=[dict(league_team_id="t",retained_salary_total=180,unresolved_owner_cases=1)])
        self.assertIsNone(plan.projected_team_cap_states[0].cap_legal)


class PackageAndReadinessTests(unittest.TestCase):
    def build(self, execution_id=None, when=None):
        pub=PublicationAuthorityPlanner().plan([]);dead=DeadCapAuthorityPlanner().plan([],2026)
        cap=CapAuthorityPlanner().plan(league_id="l",source_season=2025,target_season=2026,source_cap=200,target_cap=225,teams=[])
        return build_preparation_package(league_id="l",source_season=2025,target_season=2026,policy_id="p",
          execution_id=execution_id,owner_population_fingerprint="o",commissioner_population_fingerprint="c",
          publication_plan=pub,dead_cap_plan=dead,cap_plan=cap,owner_summary={},commissioner_summary={},generated_at=when)
    def test_timestamp_excluded_from_fingerprint(self):
        self.assertEqual(self.build(when=datetime(2026,1,1,tzinfo=timezone.utc)).preparation_fingerprint,
                         self.build(when=datetime(2026,2,1,tzinfo=timezone.utc)).preparation_fingerprint)
    def test_no_execution_blocks(self): self.assertIn("rollover execution not created",self.build().blockers)
    def test_simulator_requires_execution(self):
        with self.assertRaisesRegex(ValueError,"execution_required"): to_simulation_input(self.build(),"policy")
    def test_simulator_contract(self): self.assertEqual(to_simulation_input(self.build("e"),"policy").execution_id,"e")
    def test_readiness_progression(self):
        self.assertEqual(authority_preparation_readiness(None)["status"],"execution_control_ready")
        self.assertEqual(authority_preparation_readiness({"id":"e"})["status"],"authority_preparation_required")
        partial=[{"authority_type":"publication","status":"prepared"}]
        self.assertEqual(authority_preparation_readiness({"id":"e"},partial)["status"],"authority_preparation_in_progress")
        all_three=[{"authority_type":x,"status":"prepared"} for x in ("publication","dead_cap","salary_cap")]
        self.assertEqual(authority_preparation_readiness({"id":"e"},all_three)["status"],"dry_run_required")
    def test_validation_dependencies(self):
        plan=PublicationAuthorityPlanner().plan([])
        result=AuthorityPreparationValidator().validate("publication",plan,{"execution_exists":False,"owner_window_closed":True})
        self.assertFalse(result.valid);self.assertIn("execution_exists",result.blockers)
    def test_fingerprint_order_stable(self): self.assertEqual(material_fingerprint({"b":2,"a":1}),material_fingerprint({"a":1,"b":2}))


if __name__ == "__main__": unittest.main()
