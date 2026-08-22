from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib,re,unittest

from season_engine.rollover_control_models import *
from season_engine.rollover_control_repository import RolloverControlRepository

ROOT=Path(__file__).resolve().parents[1];MIGRATION=ROOT/"supabase/migrations/20260804_rollover_execution_control.sql";SQL=MIGRATION.read_text()
TABLES=("rollover_executions","rollover_owner_decisions","rollover_owner_decision_revisions","rollover_commissioner_reviews","rollover_commissioner_review_events","rollover_execution_plans","rollover_execution_locks","rollover_validation_results")

class Query:
 def __init__(self,rows):self.rows=rows;self.filters=[];self.bounds=None;self.order_key=None
 def select(self,*a,**k):return self
 def eq(self,k,v):self.filters.append((k,v));return self
 def order(self,k):self.order_key=k;return self
 def range(self,s,e):self.bounds=(s,e);return self
 def execute(self):
  rows=[dict(x) for x in self.rows if all(x.get(k)==v for k,v in self.filters)]
  if self.order_key:
   for index,row in enumerate(rows):row.setdefault(self.order_key,f"fixture:{index:08d}")
   rows.sort(key=lambda row:str(row[self.order_key]))
  count=len(rows)
  if self.bounds:rows=rows[self.bounds[0]:self.bounds[1]+1]
  return type("R",(),{"data":rows,"count":count})()
class Client:
 def __init__(self,rows=None):self.rows=rows or {}
 def table(self,t):return Query(self.rows.get(t,[]))

class MigrationTests(unittest.TestCase):
 def test_tables_columns_primary_and_foreign_keys(self):
  for table in TABLES:self.assertIn(f"create table if not exists public.{table}",SQL);self.assertRegex(SQL,rf"(?s){table}.*?id uuid primary key")
  for ref in ("public.leagues(id)","public.league_rollover_policies(id)","public.contract_agreements(id)","public.contract_events(id)","auth.users(id)"):self.assertIn(f"references {ref}",SQL)
 def test_constraints_indexes_and_transitions(self):
  self.assertIn("target_season=source_season+1",SQL);self.assertIn("rollover_executions_one_boundary_uidx",SQL);self.assertIn("rollover_plans_one_approved_uidx",SQL);self.assertIn("rollover_locks_one_active_scope_uidx",SQL)
  for name in ("validate_rollover_execution_transition","validate_rollover_owner_decision","validate_rollover_review_transition","protect_rollover_plan","validate_rollover_lock_transition","reject_rollover_append_only_mutation"):self.assertIn(name,SQL)
 def test_rls_least_privilege(self):
  self.assertIn("enable row level security",SQL);self.assertIn("revoke all on table",SQL);self.assertIn("grant all on table",SQL);self.assertIn("grant select on table",SQL);self.assertNotIn("grant insert",SQL.lower());self.assertNotIn(" to anon",SQL.lower())
 def test_schema_only_and_nondestructive(self):
  lowered=re.sub(r"--[^\n]*","",SQL.lower());self.assertNotRegex(lowered,r"\binsert\s+into\b");self.assertNotRegex(lowered,r"\bdelete\s+from\b");self.assertNotRegex(lowered,r"\bdrop\s+table\b");self.assertNotRegex(lowered,r"\btruncate\b")
 def test_append_only_and_empty_semantics_supported(self):
  for table in ("rollover_owner_decision_revisions","rollover_commissioner_review_events","rollover_validation_results"):self.assertIn(f"before update or delete on public.{table}",SQL)

class ModelTests(unittest.TestCase):
 def test_execution_model_timezone_boundary_and_determinism(self):
  now=datetime(2026,8,1,tzinfo=timezone.utc);x=RolloverExecution("e","l",2025,2026,"p","fp",1,RolloverExecutionStatus.DRAFT,RolloverApprovalStatus.NOT_REQUIRED,now,now+timedelta(days=7))
  self.assertEqual(model_fingerprint(x),model_fingerprint(x));self.assertEqual(deterministic_payload(x)["status"],"draft")
  with self.assertRaises(ValueError):RolloverExecution("e","l",2025,2027,"p","fp",1,RolloverExecutionStatus.DRAFT,RolloverApprovalStatus.NOT_REQUIRED)
 def test_execution_transitions_and_cancellation_boundaries(self):
  self.assertTrue(legal_transition(EXECUTION_TRANSITIONS,RolloverExecutionStatus.DRAFT,RolloverExecutionStatus.PREFLIGHT_READY));self.assertFalse(legal_transition(EXECUTION_TRANSITIONS,RolloverExecutionStatus.COMMITTED,RolloverExecutionStatus.CANCELLED));self.assertFalse(legal_transition(EXECUTION_TRANSITIONS,RolloverExecutionStatus.COMPLETED,RolloverExecutionStatus.DRAFT))
 def test_owner_transitions_preserve_review_and_execution(self):
  self.assertTrue(legal_transition(OWNER_TRANSITIONS,RolloverOwnerDecisionStatus.WAITING_FOR_OWNER,RolloverOwnerDecisionStatus.COMMISSIONER_REVIEW_REQUESTED));self.assertFalse(legal_transition(OWNER_TRANSITIONS,RolloverOwnerDecisionStatus.COMMISSIONER_REVIEW_REQUESTED,RolloverOwnerDecisionStatus.NO_RESPONSE));self.assertFalse(legal_transition(OWNER_TRANSITIONS,RolloverOwnerDecisionStatus.EXECUTED_RETAINED,RolloverOwnerDecisionStatus.WAITING_FOR_OWNER))
 def test_review_transitions(self):
  self.assertTrue(legal_transition(REVIEW_TRANSITIONS,RolloverCommissionerReviewStatus.REVIEW_REQUIRED,RolloverCommissionerReviewStatus.EVIDENCE_INCOMPLETE));self.assertFalse(legal_transition(REVIEW_TRANSITIONS,RolloverCommissionerReviewStatus.EXECUTED,RolloverCommissionerReviewStatus.CANCELLED))

class RepositoryTests(unittest.TestCase):
 def test_empty_state_never_implies_authority(self):
  repo=RolloverControlRepository(Client());self.assertEqual(repo.inspect("missing").interpretation,"no_execution_started");self.assertEqual(repo.readiness("l",2025,2026,policy_approved=True)["status"],"execution_control_ready")
 def test_typed_reads_and_no_write_capability(self):
  rows={"rollover_executions":[{"id":"e","league_id":"l","source_season":2025,"target_season":2026,"status":"draft"}]};repo=RolloverControlRepository(Client(rows));self.assertEqual(repo.find_boundary("l",2025,2026)["id"],"e");self.assertFalse(any(hasattr(repo,n) for n in ("insert","update","delete","create_execution","open_window")))

if __name__=="__main__":unittest.main()
