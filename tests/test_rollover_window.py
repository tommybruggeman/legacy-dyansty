from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import unittest

from season_engine.commissioner_policy_draft import CommissionerPolicyDraftService,RELEASE_TO_HOLD,SEVEN_DAY_NOTICE_RULE
from season_engine.rollover_window import *
from tests.test_rollover_authority import rollover_client

ROOT=Path(__file__).resolve().parents[1];SQL=(ROOT/"supabase/migrations/20260805_rollover_window_operations.sql").read_text()

def prepared_client():
 c=rollover_client();draft=CommissionerPolicyDraftService().prepare("l1",deadline=SEVEN_DAY_NOTICE_RULE,failure_to_act_outcome=RELEASE_TO_HOLD)
 c.rows["league_rollover_policies"]=[{"id":"policy","league_id":"l1","source_season":2025,"target_season":2026,"status":"approved","effective_at":None,"fingerprint":draft.fingerprint,"metadata":{"policy_payload":draft.payload}}]
 c.rows["rollover_executions"]=[];c.rows["historical_capture_executions"]=[{"id":"h","status":"validated","source_fingerprint":"history"}]
 for t in ("free_agent_publications","dead_cap_obligations","dead_cap_season_authorities","cap_season_authorities"):c.rows[t]=[]
 return c,draft

class PopulationTests(unittest.TestCase):
 def test_deterministic_order_and_classification(self):
  c,_=prepared_client();report=RolloverAuthorityService(c).build_rollover_readiness_report("l1");a=OwnerPopulationBuilder().build("l1",2025,2026,report);b=OwnerPopulationBuilder().build("l1",2025,2026,report)
  self.assertEqual(a.fingerprint,b.fingerprint);self.assertEqual(a.actual_count,1);self.assertTrue(a.cases[0].ir);self.assertFalse(a.cases[0].taxi)
  preview=CommissionerPopulationBuilder().build(report);self.assertEqual(preview.actual_count,1);self.assertEqual({x.review_type for x in preview.cases},{"active_off_roster_liability"})
 def test_duplicate_and_identity_blocking(self):
  c,_=prepared_client();report=RolloverAuthorityService(c).build_rollover_readiness_report("l1");item=next(x for x in report.roster_exceptions if x.classification=="ROSTERED_EXPIRED_POLICY_UNDEFINED")
  report=type("R",(),{"roster_exceptions":(item,item)})();result=OwnerPopulationBuilder().build("l1",2025,2026,report);self.assertTrue(any("duplicate_owner_case" in x for x in result.blockers))
 def test_active_off_roster_and_expired_unrostered_excluded(self):
  c,_=prepared_client();report=RolloverAuthorityService(c).build_rollover_readiness_report("l1");result=OwnerPopulationBuilder().build("l1",2025,2026,report);self.assertEqual([x.player_id for x in result.cases],["p3"])

class PreflightTests(unittest.TestCase):
 def request(self,draft,**changes):
  values={"league_id":"l1","source_season":2025,"target_season":2026,"policy_id":"policy","expected_policy_fingerprint":draft.fingerprint,"requested_by":"u","request_id":"r","generated_at":datetime(2026,7,1,tzinfo=timezone.utc)};values.update(changes);return RolloverPreflightRequest(**values)
 def test_stable_fingerprint_and_read_only(self):
  c,d=prepared_client();before=deepcopy(c.rows);service=RolloverPreflightService(c);a=service.run(self.request(d));b=service.run(self.request(d,generated_at=datetime(2026,7,2,tzinfo=timezone.utc)))
  self.assertEqual(a.preflight_fingerprint,b.preflight_fingerprint);self.assertEqual(c.rows,before)
 def test_policy_season_and_duplicate_mismatch_block(self):
  c,d=prepared_client();c.rows["rollover_executions"]=[{"league_id":"l1","source_season":2025,"target_season":2026,"status":"draft"}];result=RolloverPreflightService(c).run(self.request(d,expected_policy_fingerprint="bad"));self.assertIn("policy_fingerprint",result.blockers);self.assertIn("no_existing_execution",result.blockers)
 def test_changed_evidence_changes_fingerprint(self):
  c,d=prepared_client();a=RolloverPreflightService(c).run(self.request(d));c.rows["season_roster_assignments"][0]["roster_status"]="taxi";b=RolloverPreflightService(c).run(self.request(d));self.assertNotEqual(a.preflight_fingerprint,b.preflight_fingerprint)

class DeadlineAuthorizationValidationTests(unittest.TestCase):
 def test_deadline_is_exact_168_hours_and_requires_timezone(self):
  n=datetime(2026,10,30,12,tzinfo=timezone.utc);self.assertEqual(resolve_owner_deadline(n)-n,timedelta(hours=168))
  with self.assertRaises(ValueError):resolve_owner_deadline(datetime(2026,7,1))
 def test_owner_coowner_commissioner_and_spoofing(self):
  now=datetime.now(timezone.utc);deadline=now+timedelta(days=1);svc=OwnerAuthorizationService();base={"user_id":"u","league_id":"l","league_team_id":"t","role":"owner"}
  self.assertTrue(svc.authorize(user_id="u",league_id="l",decision_team_id="t",memberships=[base],execution_status="decision_window_open",deadline=deadline,now=now)["authorized"])
  self.assertFalse(svc.authorize(user_id="u",league_id="l",decision_team_id="spoof",memberships=[base],execution_status="decision_window_open",deadline=deadline,now=now)["authorized"])
  self.assertTrue(svc.authorize(user_id="c",league_id="l",decision_team_id="t",memberships=[{"user_id":"c","league_id":"l","role":"commissioner"}],execution_status="decision_window_open",deadline=deadline,now=now)["authorized"])
 def test_submission_validation_choices_deadline_and_references(self):
  now=datetime.now(timezone.utc);decision={"id":"d","player_id":"p","decision_status":"waiting_for_owner","execution_status":"pending","deadline":now+timedelta(days=1)};auth={"authorized":True}
  self.assertTrue(OwnerDecisionValidator().validate(decision,"decline",authorization=auth,now=now).valid)
  self.assertIn("recontract_references_required",OwnerDecisionValidator().validate(decision,"recontract",authorization=auth,now=now).blockers)
  self.assertIn("deadline_closed",OwnerDecisionValidator().validate({**decision,"deadline":now},"decline",authorization=auth,now=now).blockers)

class ReadinessRpcTests(unittest.TestCase):
 def test_readiness_progression(self):
  self.assertEqual(rollover_window_readiness(None)["status"],"execution_control_ready");self.assertEqual(rollover_window_readiness({"status":"preflight_ready"})["status"],"notice_window_required");self.assertEqual(rollover_window_readiness({"status":"decision_window_open"})["status"],"decision_window_open");self.assertEqual(rollover_window_readiness({"status":"decision_window_closed"})["status"],"commissioner_review_required")
 def test_rpc_migration_is_schema_only_and_atomic_functions_exist(self):
  for fn in ("create_rollover_execution","open_rollover_notice_window","submit_rollover_owner_decision","close_rollover_decision_window","cancel_rollover_execution"):self.assertIn(f"function public.{fn}",SQL)
  self.assertNotIn("truncate ",SQL.lower());self.assertNotIn("drop table",SQL.lower());self.assertIn("security definer set search_path=pg_catalog,public",SQL)
  self.assertIn("n+interval '7 days'",SQL);self.assertIn("for update",SQL);self.assertIn("commissioner_review_requested",SQL);self.assertIn("no_response",SQL)
  self.assertIn("expected_owner_count",SQL);self.assertNotIn("must contain 108",SQL);self.assertIn("expected_decision_fingerprint",SQL)
  self.assertIn("Recontract normalized references required",SQL);self.assertIn("Non-recontract choice cannot carry recontract references",SQL)
 def test_facade_only_calls_single_rpc(self):
  class C:
   def __init__(self):self.calls=[]
   def rpc(self,n,p):self.calls.append((n,p));return self
   def execute(self):return type("R",(),{"data":{"ok":True}})()
  c=C();RolloverWindowService(c).open_notice_window_as_commissioner({"x":1});self.assertEqual(c.calls,[('open_rollover_notice_window_authenticated',{'p_request':{'x':1}})])

if __name__=="__main__":unittest.main()
