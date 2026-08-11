from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
import unittest
from season_engine.authority_preparation import AuthoritySimulationInput,AuthorityDomainPlan,CapAuthorityPlan,PublicationAuthorityInstruction,DeadCapAuthorityInstruction,TeamCapProjection
from season_engine.dry_run_simulator import RolloverDryRunSimulator,RolloverDryRunValidator,dry_run_readiness,to_execution_plan_input

def team(i,**kw):
 d=dict(league_team_id=f"t{i}",source_salary_total=Decimal(100),retained_salary_total=Decimal(100),recontract_salary_total=Decimal(0),planned_release_relief=Decimal(0),planned_dead_cap=Decimal(0),cap_adjustments=Decimal(0),cap_credits_in=Decimal(0),cap_credits_out=Decimal(0),projected_cap_charge=Decimal(100),projected_cap_space=Decimal(127),cap_legal=True,unresolved_owner_cases=0,unresolved_commissioner_cases=0,blockers=(),warnings=(),evidence_fingerprint=f"e{i}");d.update(kw);return TeamCapProjection(**d)
def source(**kw):
 teams=tuple(team(i) for i in range(10));cap=CapAuthorityPlan("l",2025,2026,Decimal(227),Decimal(227),"hard",Decimal(227),Decimal(1),"nearest",Decimal(1000),Decimal(0),Decimal(0),Decimal(0),Decimal(0),teams,(),(),"c","d")
 pub=(PublicationAuthorityInstruction("p","a","t0","expired","release","approved_for_future_publication","plan_publication_at_execution",(),(),"r","o","e","i"),)
 dead=(DeadCapAuthorityInstruction("p","a","t0",None,None,None,2026,Decimal(0),"c","no_dead_cap",(),(),"e","i"),)
 owner=({"agreement_id":"a","player_id":"p","league_team_id":"t0","planned_outcome":"release_at_rollover_to_commissioner_hold","source_salary":3,"source_years_remaining":1,"source_agreement_status":"active","roster_status":"active"},)
 reviews=({"agreement_id":"a","outcome":"approve_publication"},)
 d=dict(execution_id="x",league_id="l",source_season=2025,target_season=2026,policy_fingerprint="p",owner_population_fingerprint="o",commissioner_population_fingerprint="c",authority_preparation_fingerprint="a",publication_instructions=pub,dead_cap_instructions=dead,cap_authority_plan=cap,team_cap_projections=teams,blockers=(),warnings=(),preflight_fingerprint="f",finalized_owner_outcomes=owner,finalized_commissioner_outcomes=reviews);d.update(kw);return AuthoritySimulationInput(**d)

class DryRunTests(unittest.TestCase):
 def setUp(self):self.s=RolloverDryRunSimulator();self.now=datetime(2026,1,1,tzinfo=timezone.utc)
 def test_deterministic(self):
  a=self.s.simulate(source(),generated_at=self.now);b=self.s.simulate(source(),generated_at=datetime(2026,2,1,tzinfo=timezone.utc));self.assertEqual((a.input_fingerprint,a.result_fingerprint),(b.input_fingerprint,b.result_fingerprint))
 def test_publication_and_release_separate(self):
  r=self.s.simulate(source(),generated_at=self.now);self.assertEqual(r.publication_changes[0].classification,"publish_at_execution");self.assertEqual(r.roster_changes[0].classification,"preserve_roster_assignment")
 def test_natural_dead_cap_zero(self):self.assertEqual(self.s.simulate(source(),generated_at=self.now).dead_cap_changes[0].simulated_state["amount"],"0")
 def test_ten_teams_valid(self):self.assertTrue(RolloverDryRunValidator().validate(self.s.simulate(source(),generated_at=self.now)).checks["ten_unique_teams"])
 def test_unresolved_salary_blocks(self):
  teams=tuple(team(i,recontract_salary_total=None,projected_cap_charge=None,projected_cap_space=None,cap_legal=None) if i==0 else team(i) for i in range(10));s=source(team_cap_projections=teams,cap_authority_plan=replace(source().cap_authority_plan,projected_team_cap_states=teams));self.assertIn("unresolved_salary:t0",self.s.simulate(s).blockers)
 def test_over_cap_blocks(self):
  teams=tuple(team(i,projected_cap_charge=Decimal(230),projected_cap_space=Decimal(-3),cap_legal=False) if i==0 else team(i) for i in range(10));s=source(team_cap_projections=teams,cap_authority_plan=replace(source().cap_authority_plan,projected_team_cap_states=teams));self.assertIn("hard_cap_violation:t0",self.s.simulate(s).blockers)
 def test_missing_outcomes_block(self):self.assertIn("finalized_owner_outcomes_missing",self.s.simulate(source(finalized_owner_outcomes=())).blockers)
 def test_taxi_ir(self):
  owner=({**source().finalized_owner_outcomes[0],"roster_status":"taxi"},{"agreement_id":"b","player_id":"q","league_team_id":"t1","planned_outcome":"retain","source_years_remaining":2,"roster_status":"ir"});r=self.s.simulate(source(finalized_owner_outcomes=owner));self.assertEqual((len(r.taxi_changes),len(r.ir_changes)),(1,1))
 def test_readiness(self):
  self.assertEqual(dry_run_readiness(None)["status"],"execution_control_ready");self.assertEqual(dry_run_readiness({"id":"x"})["status"],"dry_run_required")
 def test_plan_contract(self):
  r=self.s.simulate(source());v=RolloverDryRunValidator().validate(r);self.assertEqual(to_execution_plan_input(r,v).simulation_id,r.id)
 def test_no_writes(self):self.assertEqual(self.s.simulate(source()).metadata["writes_performed"],0)

if __name__=='__main__':unittest.main()
