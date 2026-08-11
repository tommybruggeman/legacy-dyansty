import unittest
from season_engine.authority_preparation import AuthorityPreparationService, PersistedAuthorityPreparation

ROW=dict(id="1",rollover_execution_id="e",league_id="l",source_season=2025,target_season=2026,
 authority_type="publication",authority_status="prepared",version=1,policy_id="p",policy_fingerprint="a"*64,
 owner_population_fingerprint="b"*64,commissioner_population_fingerprint="c"*64,evidence_fingerprint="d"*64,
 authority_fingerprint="e"*64,preparation_fingerprint="f"*64,preparation_payload={},blockers=[],warnings=[],
 prepared_by="u",prepared_at="2026-01-01T00:00:00Z",approved_by=None,approved_at=None,activated_at=None,
 superseded_at=None,superseded_by=None,cancelled_at=None,metadata={})

class SchemaContractTests(unittest.TestCase):
 def test_canonical_status(self): self.assertEqual(PersistedAuthorityPreparation.from_row(ROW).authority_status,"prepared")
 def test_legacy_status_rejected(self):
  row=dict(ROW);row["status"]=row.pop("authority_status")
  with self.assertRaisesRegex(ValueError,"authority_status"):PersistedAuthorityPreparation.from_row(row)
 def test_approval_activation_fields(self):
  row=dict(ROW,authority_status="active",approved_by="u2",approved_at="x",activated_at="y")
  parsed=PersistedAuthorityPreparation.from_row(row);self.assertEqual((parsed.approved_by,parsed.activated_at),("u2","y"))

class FakeResponse:
 def __init__(self,data):self.data=data
class FakeCall:
 def __init__(self,data):self.data=data
 def execute(self):return FakeResponse(self.data)
class FakeClient:
 def __init__(self,data):self.data=data;self.calls=[]
 def rpc(self,name,args):self.calls.append((name,args));return FakeCall(self.data)

class ServiceTests(unittest.TestCase):
 def test_complete_material_fingerprint(self):
  base={"execution_id":"e","authority_types":["publication","dead_cap","salary_cap"],"material_metadata":{"x":1}}
  a=AuthorityPreparationService.request_fingerprint("authority_preparation_prepare",base,"u")
  self.assertNotEqual(a,AuthorityPreparationService.request_fingerprint("authority_preparation_prepare",{**base,"material_metadata":{"x":2}},"u"))
  self.assertNotEqual(a,AuthorityPreparationService.request_fingerprint("authority_preparation_prepare",base,"other"))
 def test_prepare_requires_three_results(self):
  service=AuthorityPreparationService(FakeClient({"operation":"authority_preparation_prepare","preparations":[ROW]}))
  with self.assertRaisesRegex(ValueError,"exactly three"):service.prepare({})
 def test_prepare_deserializes_three(self):
  rows=[dict(ROW,authority_type=x,id=x) for x in ("publication","dead_cap","salary_cap")]
  service=AuthorityPreparationService(FakeClient({"operation":"authority_preparation_prepare","preparations":rows}))
  self.assertEqual(len(service.prepare({})["preparations"]),3)
 def test_malformed_result_rejected(self):
  with self.assertRaisesRegex(ValueError,"malformed"):AuthorityPreparationService._validate_result({})

if __name__=='__main__':unittest.main()
