from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
SQL=(ROOT/"supabase/migrations/20260806_rollover_window_operation_hardening.sql").read_text()
VERIFY=(ROOT/"supabase/verification/verify_rollover_window_operations.sql").read_text()
DB_TEST=(ROOT/"supabase/tests/20260806_rollover_window_operation_hardening_test.sql").read_text()

AUTHENTICATED=(
 "submit_rollover_owner_decision_authenticated","override_rollover_owner_decision_authenticated",
 "create_rollover_execution_authenticated","open_rollover_notice_window_authenticated",
 "close_rollover_decision_window_authenticated","cancel_rollover_execution_authenticated",
)
LEGACY=("create_rollover_execution","open_rollover_notice_window","submit_rollover_owner_decision","close_rollover_decision_window","cancel_rollover_execution")

class IdentityBoundaryTests(unittest.TestCase):
 def test_authenticated_operations_and_auth_uid(self):
  for name in AUTHENTICATED:self.assertIn(f"function public.{name}(p_request jsonb)",SQL)
  self.assertIn("uuid := auth.uid()",SQL);self.assertIn("Authenticated user required",SQL)
  self.assertIn("submitted_by does not match authenticated user",SQL)
 def test_owner_team_scope_and_commissioner_override_are_separate(self):
  self.assertIn("m.league_team_id=d.league_team_id",SQL);self.assertIn("Owner decision authority required for linked team",SQL)
  self.assertIn("commissioner_owner_override",SQL);self.assertIn("override_reason required",SQL)
  self.assertIn("'authenticated_commissioner'",SQL)
 def test_legacy_and_helper_execution_is_revoked(self):
  for name in LEGACY:self.assertRegex(SQL,rf"revoke all on function[\s\S]*public\.{name}\(jsonb\)[\s\S]*from public,anon,authenticated,service_role")
  self.assertNotIn("submit_rollover_owner_decision_internal",SQL)
 def test_authenticated_grant_is_narrow(self):
  self.assertIn("grant execute on function public.submit_rollover_owner_decision_authenticated",SQL)
  self.assertIn("to authenticated",SQL);self.assertNotIn("to anon",SQL)

class TimestampTests(unittest.TestCase):
 def test_strict_parser_and_explicit_suffix(self):
  self.assertIn("parse_required_rfc3339_instant",SQL)
  self.assertIn("(Z|[+-]\\d{2}:\\d{2})$",SQL)
  for accepted in ("2026-08-04T18:30:00Z","2026-08-04T12:30:00-06:00","2026-08-04T20:30:00+02:00","2026-08-04T18:30:00.123456Z"):
   self.assertRegex(accepted,r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
  for rejected in ("2026-08-04","2026-08-04T18:30:00","","08/04/2026 18:30","2026-08-04T18:30:00+0600"):
   self.assertNotRegex(rejected,r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
 def test_both_caller_timestamps_use_parser_and_deadline_is_168_hours(self):
  self.assertGreaterEqual(SQL.count("parse_required_rfc3339_instant"),3)
  self.assertIn("set_config('TimeZone','UTC',true)",SQL)
  base=(ROOT/"supabase/migrations/20260805_rollover_window_operations.sql").read_text()
  self.assertRegex(base,r"d=n\+interval '(7 days|168 hours)'")
  self.assertIn("interval '168 hours'",DB_TEST);self.assertIn("America/Boise",DB_TEST);self.assertIn("Pacific/Auckland",DB_TEST)

class IdempotencyTests(unittest.TestCase):
 def test_append_only_ledger_shape_and_uniqueness(self):
  for field in ("operation_type","idempotency_key","request_fingerprint","actor_user_id","caller_type","result_payload"):
   self.assertIn(field,SQL)
  self.assertIn("unique (league_id, operation_type, idempotency_key)",SQL)
  self.assertIn("rollover_operation_requests is append-only",SQL)
 def test_sha256_and_serialized_retry(self):
  self.assertIn("extensions.digest",SQL);self.assertIn("'sha256'",SQL.lower())
  self.assertIn("pg_advisory_xact_lock",SQL);self.assertIn("Idempotency key material request conflict",SQL)
  self.assertIn("prior.result_payload",SQL)
 def test_all_operations_require_keys_and_persist_results(self):
  self.assertGreaterEqual(SQL.count("rollover_operation_retry("),7)
  self.assertGreaterEqual(SQL.count("record_rollover_operation("),7)
  self.assertIn("if k is null then raise exception 'idempotency_key required'",SQL)
 def test_decision_material_fields_are_complete(self):
  for field in ("recontract_agreement_id","recontract_event_id","expected_revision_number","expected_decision_fingerprint","new_decision_fingerprint","reason","evidence"):
   self.assertIn(f"'{field}'",SQL)
 def test_notice_close_and_cancel_material_fields(self):
  for field in ("official_notice_timestamp","expected_owner_population_fingerprint","owner_population","effective_close_timestamp","expected_population_fingerprint","reason"):
   self.assertIn(f"'{field}'",SQL)

class SecurityAndVerificationArtifactTests(unittest.TestCase):
 def test_rls_and_no_authenticated_table_writes(self):
  self.assertIn("enable row level security",SQL);self.assertIn("revoke all on table public.rollover_operation_requests from public, anon, authenticated",SQL)
  self.assertNotRegex(SQL.lower(),r"grant\s+(insert|update|delete)[^;]+authenticated")
 def test_security_definer_and_fixed_search_path(self):
  self.assertGreaterEqual(SQL.count("security definer set search_path=pg_catalog,public"),10)
 def test_catalog_artifact_is_select_only(self):
  statements=[x.strip() for x in VERIFY.split(';') if x.strip() and not x.strip().startswith('--')]
  self.assertTrue(statements);self.assertTrue(all("select" in x.lower() for x in statements))
  scrubbed=re.sub(r"--.*$","",VERIFY,flags=re.M).lower()
  self.assertNotRegex(scrubbed,r"\b(insert|update|delete|create|alter|drop|truncate|call)\b")
  for catalog in ("pg_proc","pg_namespace","pg_roles","pg_indexes","pg_class","pg_policies","information_schema.routine_privileges","information_schema.table_privileges"):
   self.assertIn(catalog,VERIFY)

class ServiceFacadeTests(unittest.TestCase):
 def test_no_ambiguous_service_methods_remain(self):
  source=(ROOT/"season_engine/rollover_window.py").read_text()
  for old in ("def create_execution(","def open_notice_window(","def submit_decision(","def close_window(","def cancel_execution("):
   self.assertNotIn(old,source)
  for new in ("create_execution_as_commissioner","open_notice_window_as_commissioner","submit_owner_decision_as_authenticated_user","override_owner_decision_as_commissioner","close_window_as_commissioner","cancel_execution_as_commissioner"):
   self.assertIn(new,source)

if __name__=="__main__":unittest.main()
