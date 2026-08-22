import copy, json, os, unittest
from unittest.mock import patch
from scripts import preflight_season_rollover_production_remediation as p

class RemediationPreflightTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.contract=json.loads(p.CONTRACT.read_text())
 def snapshot(self,corrected=False):
  functions=[{"identity":x,"definition":"","authenticated_execute":False} for x in self.contract["required_functions"]]
  functions += [
   {"identity":"approve_canonical_rollover_policy_private(jsonb,uuid)","definition":"certified rollover policy boundary required exactly one canonical commissioner membership required rollover_material_fingerprint","authenticated_execute":False},
   {"identity":"validate_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid)","definition":"target_roster_owner_mismatch target_roster_duplicate_assignment","authenticated_execute":False},
   {"identity":"write_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid,uuid)","definition":"aggregate_assignment_set_hash assignment_rows_written","authenticated_execute":False},
   {"identity":"write_prepared_caps_phase3b10b_private(uuid,uuid,uuid,uuid)","definition":p.OLD_DEAD+" "+p.OLD_CAP,"authenticated_execute":False}]
  if corrected:
   functions += [{"identity":"approve_canonical_rollover_policy_authenticated(jsonb)","definition":"approve_canonical_rollover_policy_private","authenticated_execute":True},{"identity":"phase3b8a_is_preserved_off_roster_liability(uuid,uuid,text,uuid)","definition":"active_off_roster_liability preserve_active_liability review_fingerprint","authenticated_execute":False}]
   for x in functions:
    if x["identity"].startswith("validate_target"):x["definition"]="target_roster_owner_mismatch preserved_off_roster_source_assignment_conflict phase3b8a_is_preserved_off_roster_liability"
    if x["identity"].startswith("write_target"):x["definition"]="intentional_exclusion preserved_off_roster_liability_count phase3b8a_is_preserved_off_roster_liability"
    if x["identity"].startswith("write_prepared"):x["definition"]=p.NEW_DEAD+" "+p.NEW_CAP
  return {"tables":list(self.contract["required_tables"]),"roles":list(self.contract["required_roles"]),"columns":list(self.contract["required_columns"]),"functions":functions,"executions":[],"grants":[],"identity":{}}
 def test_recognized_predecessors_are_required_and_eligible(self):
  r=p.classify(self.snapshot(),self.contract);self.assertEqual("PASS",r["status"]);self.assertTrue(all(x=="REQUIRED" for x in r["migrations"].values()))
 def test_corrected_definitions_are_already_satisfied(self):
  r=p.classify(self.snapshot(True),self.contract);self.assertTrue(all(x=="ALREADY SATISFIED" for x in r["migrations"].values()))
 def test_unknown_definition_fails_closed(self):
  s=self.snapshot();next(x for x in s["functions"] if x["identity"].startswith("write_prepared"))["definition"]="unexpected"
  self.assertEqual("FAIL",p.classify(s,self.contract)["status"])
 def test_missing_dependency_fails(self):
  s=self.snapshot();s["tables"].pop();self.assertEqual("FAIL",p.classify(s,self.contract)["status"])
 def test_nonterminal_execution_fails(self):
  s=self.snapshot();s["executions"]=[{"id":"e","status":"execution_ready"}];self.assertEqual("FAIL",p.classify(s,self.contract)["status"])
 def test_query_guard(self):
  p.validate_query("select * from pg_catalog.pg_proc")
  with self.assertRaises(ValueError):p.validate_query("update x set y=1")
 def test_generated_production_sql_has_valid_statement_shape(self):
  command=p.build_read_only_command()
  scrub="";quoted=False;i=0
  while i<len(command):
   char=command[i]
   if char=="'":
    if quoted and i+1<len(command) and command[i+1]=="'":i+=2;continue
    quoted=not quoted
   elif not quoted:scrub+=char
   i+=1
  self.assertFalse(quoted,"unterminated SQL string literal")
  self.assertEqual(scrub.count("("),scrub.count(")"),"unbalanced SQL parentheses")
  statements=[x.strip() for x in scrub.split(";") if x.strip()]
  self.assertEqual(4,len(statements))
  self.assertEqual("begin transaction read only",statements[0].lower())
  self.assertRegex(statements[1].lower(),r"^set local statement_timeout\s*=\s*$")
  self.assertTrue(statements[2].lower().startswith("select jsonb_build_object("))
  self.assertEqual("rollback",statements[3].lower())
  query_statement=p.QUERY.strip()
  for malformed in (r",\s*\)$",r"\bin\s*\(\s*\)",r"^\s*\(\s*,",r"\)\s*\)\s*\)\s*\)\s*$"):
   self.assertNotRegex(query_statement,malformed)
 def test_acknowledgment_required(self):
  with patch.dict(os.environ,{},clear=True):
   with self.assertRaises(RuntimeError):p.collect()
 def test_certified_migration_hashes(self):self.assertEqual([],p.verify_files(self.contract))

if __name__=="__main__":unittest.main()
