from dataclasses import replace
from decimal import Decimal
import unittest
from tests.test_rollover_dry_run_simulator import source
from season_engine.authority_preparation import DeadCapAuthorityInstruction,PublicationAuthorityInstruction
from season_engine.dry_run_simulator import PersistedDryRunSimulation,RolloverDryRunSimulator,TrustedDryRunCancellationService,TrustedDryRunGenerationService

FP="a"*64
ROW=dict(id="s",rollover_execution_id="e",league_id="l",source_season=2025,target_season=2026,simulation_version=1,simulator_version="v1",simulation_status="valid",input_fingerprint=FP,result_fingerprint=FP,policy_fingerprint=FP,preflight_fingerprint=FP,owner_population_fingerprint=FP,commissioner_population_fingerprint=FP,authority_preparation_fingerprint=FP,result_payload={},blockers=[],warnings=[],valid=True,executable=True,plan_eligible=True)
class Response:
 def __init__(self,data):self.data=data
class Call:
 def __init__(self,data):self.data=data
 def execute(self):return Response(self.data)
class Client:
 def __init__(self,data):self.data=data;self.calls=[]
 def rpc(self,name,args):
  self.calls.append((name,args))
  if name=="rollover_material_fingerprint":
   return Call(FP)
  return Call(self.data)
class Query:
 def __init__(self,rows):self.rows=rows
 def select(self,*a):return self
 def eq(self,*a):return self
 def execute(self):return Response(self.rows)
class EvidenceClient(Client):
 def __init__(self,rows):super().__init__({"authorized":True,"actor_user_id":"u"});self.rows=rows
 def table(self,name):return Query(self.rows[name])

class PersistedTests(unittest.TestCase):
 def test_preflight_required(self):
  row=dict(ROW);row.pop("preflight_fingerprint")
  with self.assertRaisesRegex(ValueError,"preflight"):PersistedDryRunSimulation.from_row(row)
 def test_malformed_preflight(self):
  with self.assertRaisesRegex(ValueError,"preflight"):PersistedDryRunSimulation.from_row(dict(ROW,preflight_fingerprint="bad"))

class TrustBoundaryTests(unittest.TestCase):
 def service(self):return TrustedDryRunGenerationService(Client({"authorized":True,"actor_user_id":"u"}),Client({"simulation":ROW}))
 def test_caller_conclusions_rejected(self):
  for field in ("result_payload","valid","executable","plan_eligible","simulation_status","blockers","warnings"):
   with self.assertRaisesRegex(ValueError,"forbidden"):self.service().generate(source(),{field:True})
 def test_canonical_simulator_and_validator_used(self):
  trusted=self.service()
  row=trusted.generate(source(),{})
  self.assertTrue(row.valid)
  persistence_calls=[
   args for name,args in trusted.service_client.calls
   if name=="persist_rollover_dry_run_service"
  ]
  self.assertEqual(len(persistence_calls),1)
  body=persistence_calls[0]["p_request"]
  self.assertIn("canonical_result",body)
  self.assertIn("validation",body["canonical_result"])
  self.assertIn("canonical_input_transport_fingerprint",body)
  self.assertEqual(len(body["canonical_input_transport_fingerprint"]),64)
 def test_preflight_changes_input_fingerprint(self):
  from season_engine.dry_run_simulator import RolloverDryRunSimulator
  a=RolloverDryRunSimulator().simulate(source()).input_fingerprint;b=RolloverDryRunSimulator().simulate(replace(source(),preflight_fingerprint="changed")).input_fingerprint;self.assertNotEqual(a,b)
 def test_expected_input_mismatch(self):
  with self.assertRaisesRegex(ValueError,"input fingerprint mismatch"):self.service().generate(source(),{"expected_input_fingerprint":"bad"})
 def test_material_incomplete_rejected_by_simulator(self):
  from season_engine.dry_run_simulator import RolloverDryRunSimulator
  result=RolloverDryRunSimulator().simulate(replace(source(),finalized_owner_outcomes=()));self.assertIn("finalized_owner_outcomes_missing",result.blockers)
 def test_authoritative_reconstruction_orders_evidence(self):
  preparations=[{"authority_type":x,"authority_status":"prepared","id":x,"version":1,"authority_fingerprint":FP,"evidence_fingerprint":FP,"preparation_fingerprint":FP} for x in ("salary_cap","publication","dead_cap")]
  rows={"rollover_executions":[{"id":"e"}],"rollover_owner_decisions":[{"agreement_id":"b","player_id":"2","decision_status":"planned_release"},{"agreement_id":"a","player_id":"1","decision_status":"planned_retention"}],"rollover_commissioner_reviews":[{"agreement_id":"a","player_id":"1","review_type":"x","review_state":"approved"}],"rollover_authority_preparations":preparations}
  service=TrustedDryRunGenerationService(EvidenceClient(rows),Client({"simulation":ROW}));built,evidence=service.build_authority_simulation_input("e",lambda evidence:source())
  self.assertEqual([x["agreement_id"] for x in evidence["owner_outcomes"]],["a","b"]);self.assertEqual([x["authority_type"] for x in evidence["preparations"]],["dead_cap","publication","salary_cap"])

class CancellationTests(unittest.TestCase):
 def simulation(self,status="valid"):
  return PersistedDryRunSimulation.from_row(dict(ROW,simulation_status=status,**({"cancelled_at":"2026-01-01T00:00:00Z"} if status=="cancelled" else {})))
 def request(self,**changes):
  request=dict(TrustedDryRunCancellationService.build_request(self.simulation(),idempotency_key="cancel-1",reason="commissioner correction"))
  request.update(changes);return request
 def test_preflight_required_and_malformed(self):
  with self.assertRaisesRegex(ValueError,"expected_preflight_fingerprint"):
   request=self.request();request.pop("expected_preflight_fingerprint");TrustedDryRunCancellationService.validate_request(request)
  with self.assertRaisesRegex(ValueError,"malformed expected_preflight_fingerprint"):
   TrustedDryRunCancellationService.validate_request(self.request(expected_preflight_fingerprint="bad"))
 def test_complete_identity_is_sent(self):
  client=Client({"simulation":dict(ROW,simulation_status="cancelled",cancelled_at="2026-01-01T00:00:00Z")});service=TrustedDryRunCancellationService(client)
  before=self.request();after=service.cancel(before)
  sent=client.calls[0][1]["p_request"]
  self.assertEqual(sent["expected_preflight_fingerprint"],FP);self.assertEqual(after.result_payload,{})
  self.assertEqual(after.input_fingerprint,FP);self.assertEqual(after.result_fingerprint,FP);self.assertIsNotNone(after.cancelled_at)
 def test_response_evidence_change_rejected(self):
  for field in ("input_fingerprint","result_fingerprint","preflight_fingerprint"):
   client=Client({"simulation":dict(ROW,simulation_status="cancelled",cancelled_at="2026-01-01T00:00:00Z",**{field:"b"*64})})
   with self.assertRaisesRegex(ValueError,"changed"):TrustedDryRunCancellationService(client).cancel(self.request())
 def test_terminal_states_rejected_locally(self):
  for status in ("approved_for_plan","superseded","cancelled"):
   with self.assertRaisesRegex(ValueError,"not cancellable"):
    TrustedDryRunCancellationService.build_request(self.simulation(status),idempotency_key="x",reason="x")
 def test_cancelled_response_requires_database_timestamp(self):
  client=Client({"simulation":dict(ROW,simulation_status="cancelled")})
  with self.assertRaisesRegex(ValueError,"cancelled_at"):TrustedDryRunCancellationService(client).cancel(self.request())
 def test_material_request_changes_are_visible(self):
  base=self.request()
  for key,value in (("reason","changed"),("expected_preflight_fingerprint","b"*64),("expected_input_fingerprint","b"*64),("expected_result_fingerprint","b"*64),("expected_simulation_version",2)):
   changed=self.request(**{key:value});self.assertNotEqual(base,changed)

class IncompleteInstructionTests(unittest.TestCase):
 def setUp(self):self.simulator=RolloverDryRunSimulator()
 def test_missing_recontract_salary_and_term(self):
  owner=({**source().finalized_owner_outcomes[0],"planned_outcome":"planned_retention"},)
  blockers=self.simulator.simulate(source(finalized_owner_outcomes=owner)).blockers
  self.assertIn("recontract_salary_required",blockers);self.assertIn("recontract_term_required",blockers)
 def test_duplicate_publication_blocks(self):
  item=source().publication_instructions[0]
  self.assertIn("duplicate_publication",self.simulator.simulate(source(publication_instructions=(item,item))).blockers)
 def test_active_publication_blocker_is_preserved(self):
  item=replace(source().publication_instructions[0],publication_blockers=("active_agreement_conflict",))
  result=self.simulator.simulate(source(publication_instructions=(item,)))
  self.assertIn("active_agreement_conflict",result.blockers);self.assertEqual(result.metadata["writes_performed"],0)
 def test_nonzero_dead_cap_requires_event(self):
  item=replace(source().dead_cap_instructions[0],calculated_amount=Decimal("5"),salary_basis=Decimal("10"))
  self.assertIn("qualifying_event_required",self.simulator.simulate(source(dead_cap_instructions=(item,))).blockers)
 def test_one_dollar_exemption_is_zero(self):
  item=replace(source().dead_cap_instructions[0],calculated_amount=Decimal("5"),salary_basis=Decimal("1"),qualifying_event_id="event")
  result=self.simulator.simulate(source(dead_cap_instructions=(item,)))
  self.assertEqual(result.dead_cap_changes[0].simulated_state["amount"],"0");self.assertEqual(result.metadata["writes_performed"],0)
 def test_complete_over_cap_is_blocked_not_malformed(self):
  from tests.test_rollover_dry_run_simulator import team
  teams=tuple(team(i,projected_cap_charge=Decimal("230"),projected_cap_space=Decimal("-3"),cap_legal=False) if i==0 else team(i) for i in range(10))
  result=self.simulator.simulate(source(team_cap_projections=teams,cap_authority_plan=replace(source().cap_authority_plan,projected_team_cap_states=teams)))
  self.assertTrue(result.valid);self.assertFalse(result.executable);self.assertIn("hard_cap_violation:t0",result.blockers)

if __name__=='__main__':unittest.main()
