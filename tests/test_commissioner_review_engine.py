from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import re
import unittest

from season_engine.commissioner_review import *

ROOT=Path(__file__).resolve().parents[1]
SQL=(ROOT/"supabase/migrations/20260807_commissioner_review_engine.sql").read_text()
VERIFY=(ROOT/"supabase/verification/verify_commissioner_review_engine.sql").read_text()
DB_TEST=(ROOT/"supabase/tests/20260807_commissioner_review_engine_test.sql").read_text()

def item(i,classification,name=None,salary="3.0"):
 return SimpleNamespace(classification=classification,player_id=str(i),player_name=name or f"Player {i}",team_id=f"team-{i}",agreement_id=f"agreement-{i}",contract_status="active" if classification.startswith("ACTIVE") else "expired",roster_status="unrostered",evidence={"salary":salary,"years_remaining":1})

def report13():
 return SimpleNamespace(roster_exceptions=tuple([item(1,"ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED","Jalen Milroe"),item(2,"ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED","Tre Harris")]+[item(i,"EXPIRED_UNROSTERED_PUBLICATION_PENDING") for i in range(3,14)]))

def review(kind="active_off_roster_liability",state="under_review",status="active",salary="3.0"):
 return {"id":"review","review_type":kind,"review_state":state,"player_id":"12510","league_team_id":"team","agreement_id":"agreement","agreement_status":status,"roster_status":"unrostered","source_salary":salary}

class PopulationTests(unittest.TestCase):
 def test_deterministic_thirteen_case_population(self):
  with patch("season_engine.commissioner_review.RolloverAuthorityService") as service:
   service.return_value.build_rollover_readiness_report.return_value=report13();b=CommissionerPopulationBuilder();a=b.build(object(),"league",2025,2026);c=b.build(object(),"league",2025,2026)
  self.assertEqual(a.actual_count,13);self.assertEqual(a.difference,0);self.assertEqual(a.fingerprint,c.fingerprint);self.assertFalse(a.blockers)
  self.assertEqual(sum(x.review_type=="active_off_roster_liability" for x in a.cases),2);self.assertEqual(sum(x.review_type=="expired_unrostered_publication_candidate" for x in a.cases),11)
 def test_milroe_and_harris_preserved(self):
  with patch("season_engine.commissioner_review.RolloverAuthorityService") as service:
   service.return_value.build_rollover_readiness_report.return_value=report13();p=CommissionerPopulationBuilder().build(object(),"league",2025,2026)
  for case in [x for x in p.cases if x.canonical_player_name in {"Jalen Milroe","Tre Harris"}]:
   self.assertEqual(case.source_salary,"3.0");self.assertEqual(case.target_contract_state,"active_liability");self.assertEqual(case.publication_status,"blocked");self.assertEqual(case.acquisition_status,"blocked");self.assertEqual(case.second_agreement_status,"blocked");self.assertFalse(case.evidence["termination_inferred"]);self.assertFalse(case.evidence["dead_cap_inferred"])
 def test_owner_escalation_conflict_and_duplicate_detection(self):
  escalation={"player_id":"x","player_name":"X","league_team_id":"team","agreement_id":"a","review_type":"owner_escalation","evidence":{}}
  conflict={**escalation,"review_type":"identity_conflict"}
  with patch("season_engine.commissioner_review.RolloverAuthorityService") as service:
   service.return_value.build_rollover_readiness_report.return_value=report13();p=CommissionerPopulationBuilder().build(object(),"league",2025,2026,[escalation,escalation],[conflict])
  self.assertTrue(any("duplicate_commissioner_case" in x for x in p.blockers));self.assertIn("identity_conflict",{x.review_type for x in p.cases})
 def test_evidence_change_changes_population_fingerprint(self):
  first=report13();second=report13();second.roster_exceptions[0].evidence["salary"]="4.0"
  with patch("season_engine.commissioner_review.RolloverAuthorityService") as service:
   service.return_value.build_rollover_readiness_report.side_effect=[first,second];b=CommissionerPopulationBuilder();a=b.build(object(),"league",2025,2026);c=b.build(object(),"league",2025,2026)
  self.assertNotEqual(a.fingerprint,c.fingerprint)

class OutcomeValidationTests(unittest.TestCase):
 def setUp(self):self.v=CommissionerReviewValidator();self.now=datetime(2026,8,1,tzinfo=timezone.utc)
 def test_outcome_matrix_is_narrow(self):
  self.assertIn("preserve_active_liability",OUTCOME_MATRIX["active_off_roster_liability"]);self.assertNotIn("approve_publication",OUTCOME_MATRIX["active_off_roster_liability"])
  self.assertIn("approve_publication",OUTCOME_MATRIX["expired_unrostered_publication_candidate"]);self.assertNotIn("approve_termination",OUTCOME_MATRIX["expired_unrostered_publication_candidate"])
 def test_safe_active_liability_preservation(self):
  result=self.v.validate(review(),"preserve_active_liability",evidence={"duplicate_active_agreement":False},retained_agreement_id="agreement",validated_at=self.now);self.assertTrue(result.valid);self.assertTrue(result.valid_for_plan)
 def test_active_liability_publication_and_second_agreement_rejected(self):
  result=self.v.validate(review(),"approve_publication",evidence={"publication_eligible":True,"duplicate_active_agreement":True},validated_at=self.now);self.assertFalse(result.valid);self.assertIn("outcome_not_allowed_for_review_type",result.blockers)
  retained=self.v.validate(review(),"retain_contract",evidence={"duplicate_active_agreement":True},validated_at=self.now);self.assertIn("retention_conflict",retained.blockers)
 def test_termination_requires_complete_evidence(self):
  bad=self.v.validate(review(),"approve_termination",evidence={},termination_event_id="event",validated_at=self.now);self.assertIn("termination_evidence_incomplete",bad.blockers)
  good=self.v.validate(review(),"approve_termination",evidence={"termination_authority":"commissioner","termination_reason":"early release","effective_season":2026,"validated_agreement_state":True},termination_event_id="event",validated_at=self.now);self.assertTrue(good.valid)
 def test_dead_cap_requires_qualifying_event(self):
  r=review("contract_conflict");bad=self.v.validate(r,"approve_dead_cap",evidence={"dead_cap_amount":3,"dead_cap_calculation_fingerprint":"fp"},validated_at=self.now);self.assertIn("dead_cap_evidence_incomplete",bad.blockers)
 def test_publication_requires_eligibility_and_no_active_agreement(self):
  r=review("expired_unrostered_publication_candidate",status="expired");self.assertIn("publication_evidence_incomplete",self.v.validate(r,"approve_publication",evidence={},publication_reference="future",validated_at=self.now).blockers)
  self.assertTrue(self.v.validate(r,"approve_publication",evidence={"publication_eligible":True,"publication_authority_initialized":False},publication_reference="future",validated_at=self.now).valid)
 def test_validator_never_claims_execution_ready(self):
  r=self.v.validate(review(),"preserve_active_liability",evidence={},validated_at=self.now);self.assertEqual(r.authority_checks["publication"],"deferred_to_authority_preparation")

class StateReadinessPlanTests(unittest.TestCase):
 def test_legal_state_machine_and_terminal_states(self):
  self.assertIn("under_review",LEGAL_TRANSITIONS["pending"]);self.assertIn("superseded",LEGAL_TRANSITIONS["approved"]);self.assertFalse(LEGAL_TRANSITIONS["executed"]);self.assertFalse(LEGAL_TRANSITIONS["cancelled"])
 def test_readiness_progression(self):
  self.assertEqual(commissioner_review_readiness(None,[])["status"],"execution_control_ready")
  closed={"status":"decision_window_closed"};self.assertEqual(commissioner_review_readiness(closed,[])["status"],"commissioner_review_required")
  self.assertEqual(commissioner_review_readiness(closed,[{"id":"r","review_state":"under_review"}])["status"],"commissioner_review_in_progress")
  self.assertEqual(commissioner_review_readiness(closed,[{"id":"r","review_state":"blocked"}])["status"],"commissioner_review_blocked")
  complete=commissioner_review_readiness(closed,[{"id":"r","review_state":"approved"}]);self.assertEqual(complete["status"],"authority_preparation_required");self.assertEqual(complete["review_status"],"commissioner_review_complete")
 def test_plan_instruction_is_deterministic_and_nonexecuting(self):
  value={"id":"r","player_id":"p","league_team_id":"t","agreement_id":"a","outcome":"approve_publication","evidence_fingerprint":"e","review_fingerprint":"f"};a=to_plan_instruction(value);b=to_plan_instruction(value);self.assertEqual(a,b);self.assertEqual(a.planned_publication_action,"candidate");self.assertEqual(a.planned_roster_action,"future_plan_only")

class MigrationSecurityTests(unittest.TestCase):
 def test_rpc_inventory_and_auth_helpers(self):
  for name in ("initialize_rollover_commissioner_reviews_authenticated","begin_rollover_commissioner_review_authenticated","submit_rollover_commissioner_review_authenticated","supersede_rollover_commissioner_review_authenticated","cancel_rollover_commissioner_review_authenticated"):
   self.assertIn(f"function public.{name}(p_request jsonb)",SQL)
  self.assertGreaterEqual(SQL.count("require_authenticated_user()"),5);self.assertGreaterEqual(SQL.count("require_commissioner_authority"),5)
 def test_operation_ledger_and_material_fingerprints(self):
  self.assertGreaterEqual(SQL.count("rollover_operation_retry("),5);self.assertGreaterEqual(SQL.count("record_rollover_operation("),5);self.assertGreaterEqual(SQL.count("rollover_material_fingerprint("),10)
  for field in ("proposed_outcome","termination_event_id","dead_cap_event_id","publication_reference","retained_agreement_id","expected_revision_number","expected_review_fingerprint"):
   self.assertIn(field,SQL)
 def test_atomic_initialization_and_no_domain_mutations(self):
  self.assertIn("for update",SQL);self.assertIn("Commissioner population fingerprint mismatch",SQL);self.assertIn("Frozen commissioner review count mismatch",SQL);self.assertIn("on conflict(idempotency_key) do nothing",SQL)
  scrub=re.sub(r"--.*$","",SQL,flags=re.M).lower();self.assertNotIn("insert into public.free_agent_publications",scrub);self.assertNotIn("insert into public.dead_cap_obligations",scrub);self.assertNotIn("update public.contract_agreements",scrub);self.assertNotIn("update public.season_roster_assignments",scrub)
 def test_state_trigger_append_only_and_supersession_guards(self):
  self.assertIn("Executed commissioner review is immutable",SQL);self.assertIn("Review cannot be superseded after final plan approval",SQL);self.assertIn("rollover_commissioner_review_state_guard",SQL);self.assertIn("rollover_commissioner_review_events",SQL)
 def test_grants_are_authenticated_only(self):
  self.assertIn("from public,anon,authenticated,service_role",SQL);self.assertIn("to authenticated",SQL);self.assertNotIn("to anon",SQL)
 def test_verification_is_select_only_and_complete(self):
  scrub=re.sub(r"--.*$","",VERIFY,flags=re.M).lower();self.assertNotRegex(scrub,r"\b(insert|update|delete|create|alter|drop|truncate|call)\b")
  for name in ("pg_proc","pg_namespace","pg_roles","pg_indexes","pg_class","pg_constraint","pg_policies","information_schema.routine_privileges","information_schema.table_privileges","information_schema.columns"):self.assertIn(name,VERIFY)
 def test_database_test_is_transactional_and_rollback_only(self):
  self.assertRegex(DB_TEST.lower(),r"^--[\s\S]*\bbegin;");self.assertTrue(DB_TEST.rstrip().lower().endswith("rollback;"));self.assertIn("Illegal approved-to-under-review transition",DB_TEST);self.assertIn("Review event history was mutable",DB_TEST);self.assertIn("Material idempotency conflict was accepted",DB_TEST)
 def test_facade_calls_only_authenticated_rpcs(self):
  class C:
   def __init__(self):self.calls=[]
   def rpc(self,n,p):self.calls.append(n);return self
   def execute(self):return SimpleNamespace(data={})
  c=C();s=CommissionerReviewService(c);s.initialize({});s.begin({});s.submit({});s.supersede({});s.cancel({});self.assertTrue(all(x.endswith("_authenticated") for x in c.calls))

if __name__=="__main__":unittest.main()
