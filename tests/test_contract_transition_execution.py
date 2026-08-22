from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import patch
import unittest

from contract_engine.transition_execution_models import EXECUTOR_VERSION
from contract_engine.transition_execution_service import build_contract_transition_execution_request
from contract_engine.transition_execution_service import ContractTransitionExecutionService
from contract_engine.transition_execution_service import ContractTransitionRetryError
from contract_engine.transition_execution_validator import validate_contract_transition_execution
from contract_engine.transition_executor import ContractTransitionExecutionError,simulate_atomic_contract_transition
from tests.test_contract_transition import fixture,plan


SQL=Path("supabase/migrations/20260803_contract_transition_execution.sql").read_text()


class Query:
    def __init__(self,client,table):self.client=client;self.table=table;self.filters=[]
    def select(self,*args,**kwargs):return self
    def eq(self,key,value):self.filters.append((key,value));return self
    def execute(self):
        rows=[dict(x) for x in self.client.rows.get(self.table,[])]
        for key,value in self.filters:rows=[x for x in rows if str(x.get(key))==str(value)]
        return SimpleNamespace(data=rows)


class RetryClient:
    def __init__(self,execution=None,drift=None,with_execution=True):
        key="contract-transition:league-1:2025:2026:v1"
        result={"status":"validated","execution_id":"execution-1","result_fingerprint":"result-fp","persisted":{
            "agreements":211,"active_agreements":92,"expired_agreements":119,"satisfied_2025":211,"active_2026":92,
            "scheduled_2027":32,"expired_events":119,"contract_events":330}}
        execution=execution or {"id":"execution-1","league_id":"league-1","source_league_season_id":"ls25","target_league_season_id":"ls26",
            "source_season":2025,"target_season":2026,"transition_key":key,"expected_source_fingerprint":"source-fp","plan_fingerprint":"plan-fp",
            "request_version":"v1","planner_version":"contract-transition-v1","executor_version":"contract-transition-executor-v1",
            "status":"validated","started_at":"original-start","completed_at":"original-complete","result":result}
        agreements=[{"id":f"a{i}","league_id":"league-1","status":"active" if i<92 else "expired"} for i in range(211)]
        seasons=[{"id":f"s25-{i}","league_id":"league-1","season":2025,"obligation_status":"satisfied"} for i in range(211)]
        seasons += [{"id":f"s26-{i}","league_id":"league-1","season":2026,"obligation_status":"active"} for i in range(92)]
        seasons += [{"id":f"s27-{i}","league_id":"league-1","season":2027,"obligation_status":"scheduled"} for i in range(32)]
        events=[{"id":f"import-{i}","league_id":"league-1","event_type":"imported","metadata":{}} for i in range(211)]
        events += [{"id":f"expired-{i}","league_id":"league-1","event_type":"expired","metadata":{"transition_key":key}} for i in range(119)]
        self.rows={"league_seasons":[{"id":"ls25","league_id":"league-1","season":2025},{"id":"ls26","league_id":"league-1","season":2026}],
            "contract_transition_executions":[execution] if with_execution else [],"contract_agreements":agreements,
            "contract_seasons":seasons,"contract_events":events}; self.rpc_calls=[]
        if drift:self.rows[drift].pop()
    def table(self,name):return Query(self,name)
    def rpc(self,name,payload):self.rpc_calls.append((name,payload));return SimpleNamespace(execute=lambda:SimpleNamespace(data={"status":"validated"}))


RETRY_KW=dict(league_id="league-1",source_season=2025,target_season=2026,expected_source_fingerprint="source-fp",expected_plan_fingerprint="plan-fp")


def setup(years=1,dry_run=False):
    p=plan(fixture(years)); request=build_contract_transition_execution_request(plan=p,dry_run=dry_run,
        expected_source_fingerprint=p.source_fingerprint,expected_plan_fingerprint=p.plan_fingerprint)
    # The unit fixture is intentionally smaller than the approved production plan.
    request=replace(request,expected_counts={k:p.counts.get(k,0) for k in request.expected_counts})
    state={"agreements":[{"id":x["agreement_id"],"status":"active","salary":"12.50","team":"t1","player":"p1"} for x in p.classifications],
        "seasons":[{"contract_id":x["agreement_id"],"season":2025,"obligation_status":"active","salary":"12.50","team":"t1","player":"p1"} for x in p.classifications],
        "events":[],"executions":{},"league_seasons":[{"season":2025,"status":"active"},{"season":2026,"status":"scheduled"}],
        "legacy_contracts":[{"id":"legacy","salary":"12.50","years":years}],"rosters":[{"player":"p1"}],"dead_cap":[],"free_agents":[],"cap_adjustments":[],"draft_picks":[],"history":[{"frozen":True}]}
    if years>=2:state["seasons"].append({"contract_id":p.classifications[0]["agreement_id"],"season":2026,"obligation_status":"scheduled","salary":"12.50","team":"t1","player":"p1"})
    if years>=3:state["seasons"].append({"contract_id":p.classifications[0]["agreement_id"],"season":2027,"obligation_status":"scheduled","salary":"12.50","team":"t1","player":"p1"})
    return p,request,state


class ContractTransitionExecutionTests(unittest.TestCase):
    def test_request_requires_explicit_boolean(self):
        p=plan(fixture(1))
        with self.assertRaises(ValueError):build_contract_transition_execution_request(plan=p,dry_run=None,expected_source_fingerprint=p.source_fingerprint,expected_plan_fingerprint=p.plan_fingerprint)

    def test_apply_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):ContractTransitionExecutionService(object()).apply(confirm_apply=False)

    def test_identical_service_retry_bypasses_planner_rpc_and_preserves_audit(self):
        client=RetryClient(); before=deepcopy(client.rows)
        with patch("contract_engine.transition_execution_service.ContractTransitionService.plan",side_effect=AssertionError("planner called")):
            result=ContractTransitionExecutionService(client).apply(**RETRY_KW,confirm_apply=True)
        self.assertTrue(result["idempotent"]);self.assertEqual(result["execution_id"],"execution-1")
        self.assertEqual(result["result_fingerprint"],"result-fp");self.assertEqual(client.rpc_calls,[]);self.assertEqual(client.rows,before)

    def test_retry_validates_post_state_and_blocks_drift(self):
        for table in ("contract_agreements","contract_seasons","contract_events"):
            with self.assertRaises(ContractTransitionRetryError) as caught:
                ContractTransitionExecutionService(RetryClient(drift=table)).apply(**RETRY_KW,confirm_apply=True)
            self.assertEqual(caught.exception.code,"persisted_state_drift")

    def test_retry_identity_conflicts_block(self):
        cases=({"expected_source_fingerprint":"different"},{"expected_plan_fingerprint":"different"},
            {"request_version":"v2"},{"planner_version":"v2"},{"executor_version":"v2"})
        for changes in cases:
            kwargs={**RETRY_KW,**changes}
            with self.assertRaises(ContractTransitionRetryError) as caught:
                ContractTransitionExecutionService(RetryClient()).apply(**kwargs,confirm_apply=True)
            self.assertIn(caught.exception.code,{"conflicting_retry","invalid_version"})

    def test_applying_and_failed_execution_states_block(self):
        for status in ("applying","failed"):
            client=RetryClient();client.rows["contract_transition_executions"][0]["status"]=status
            with self.assertRaises(ContractTransitionRetryError) as caught:
                ContractTransitionExecutionService(client).apply(**RETRY_KW,confirm_apply=True)
            self.assertEqual(caught.exception.code,f"execution_{status}")

    def test_no_execution_uses_pretransition_preview_and_rpc(self):
        client=RetryClient(with_execution=False);service=ContractTransitionExecutionService(client)
        request=SimpleNamespace(payload=lambda:{"request":"payload"})
        preview=SimpleNamespace(safe_to_apply=True,request=request,blocking_errors=())
        with patch.object(service,"preview",return_value=preview) as planner_path:
            result=service.apply(**RETRY_KW,confirm_apply=True)
        planner_path.assert_called_once();self.assertEqual(len(client.rpc_calls),1);self.assertEqual(result["status"],"validated")

    def test_no_execution_invalid_plan_still_blocks(self):
        client=RetryClient(with_execution=False);service=ContractTransitionExecutionService(client)
        preview=SimpleNamespace(safe_to_apply=False,blocking_errors=({"code":"planner_blocked"},))
        with patch.object(service,"preview",return_value=preview):
            with self.assertRaises(ValueError):service.apply(**RETRY_KW,confirm_apply=True)
        self.assertEqual(client.rpc_calls,[])

    def test_request_contains_versions_and_canonical_key(self):
        p,r,_=setup(); self.assertEqual(r.executor_version,EXECUTOR_VERSION); self.assertEqual(r.transition_key,"contract-transition:league-1:2025:2026:v1")

    def test_fingerprint_and_version_mismatches_block(self):
        p,r,_=setup()
        for changed in (replace(r,actual_source_fingerprint="bad"),replace(r,actual_plan_fingerprint="bad"),replace(r,request_version="v2"),replace(r,executor_version="v2"),replace(r,transition_key="bad")):
            self.assertFalse(validate_contract_transition_execution(changed,p)["safe_to_apply"])

    def test_approved_production_shape_validates(self):
        p,r,_=setup(); counts={"agreements":211,"continues":92,"expires":119,"source_obligations":211,
            "target_obligations":92,"season_2027_obligations":32,"invalid":0,"already_transitioned":0}
        approved=SimpleNamespace(request=p.request,source_fingerprint=p.source_fingerprint,plan_fingerprint=p.plan_fingerprint,
            safe_to_transition=True,counts=counts,warnings=())
        self.assertTrue(validate_contract_transition_execution(replace(r,expected_counts=counts),approved)["safe_to_apply"])

    def test_dry_run_has_zero_writes(self):
        p,r,state=setup(dry_run=True); result_state,result=simulate_atomic_contract_transition(state,p,r)
        self.assertIs(result_state,state); self.assertEqual(result["status"],"dry_run_validated"); self.assertEqual(state["executions"],{})

    def test_expiring_lifecycle_mutations(self):
        p,r,state=setup(1); after,result=simulate_atomic_contract_transition(state,p,r)
        self.assertEqual(after["seasons"][0]["obligation_status"],"satisfied")
        self.assertEqual(after["agreements"][0]["status"],"expired")
        self.assertEqual(after["events"][0]["event_type"],"expired")
        self.assertFalse(after["events"][0]["metadata"]["dead_cap_consequence"])

    def test_continuing_lifecycle_and_2027_unchanged(self):
        p,r,state=setup(3); after,_=simulate_atomic_contract_transition(state,p,r)
        self.assertEqual([x["obligation_status"] for x in after["seasons"]],["satisfied","active","scheduled"])
        self.assertEqual(after["agreements"][0]["status"],"active"); self.assertEqual(after["events"],[])

    def test_identity_and_salary_are_unchanged(self):
        p,r,state=setup(2); before=[dict(x) for x in state["seasons"]]; after,_=simulate_atomic_contract_transition(state,p,r)
        for old,new in zip(before,after["seasons"]):
            self.assertEqual((old["salary"],old["team"],old["player"]),(new["salary"],new["team"],new["player"]))

    def test_outside_domains_are_unchanged(self):
        p,r,state=setup(); after,_=simulate_atomic_contract_transition(state,p,r)
        for key in ("league_seasons","legacy_contracts","rosters","dead_cap","free_agents","cap_adjustments","draft_picks","history"):
            self.assertEqual(after[key],state[key])

    def test_atomic_failures_commit_nothing(self):
        for point in ("validation","source_update","agreement_expiration","event_creation"):
            p,r,state=setup()
            with self.assertRaises(ContractTransitionExecutionError):simulate_atomic_contract_transition(state,p,r,fail_at=point)
            self.assertEqual(state["seasons"][0]["obligation_status"],"active");self.assertEqual(state["events"],[]);self.assertEqual(state["executions"],{})

    def test_identical_retry_is_idempotent_and_conflict_fails(self):
        p,r,state=setup(); after,first=simulate_atomic_contract_transition(state,p,r); again,second=simulate_atomic_contract_transition(after,p,r)
        self.assertIs(again,after);self.assertTrue(second["idempotent"]);self.assertEqual(len(after["events"]),1);self.assertEqual(len(after["executions"]),1)
        with self.assertRaises(ContractTransitionExecutionError):simulate_atomic_contract_transition(after,p,replace(r,expected_plan_fingerprint="different"))

    def test_migration_has_atomic_lock_permissions_and_no_outside_writes(self):
        lower=SQL.lower(); self.assertIn("pg_advisory_xact_lock",lower);self.assertIn("security definer",lower)
        self.assertIn("grant execute on function public.apply_contract_transition(jsonb) to service_role",lower)
        for table in ("league_seasons","contracts","season_roster_assignments","dead_cap_ledger","cap_adjustments","draft_picks","player_universe"):
            self.assertNotIn(f"update public.{table}",lower);self.assertNotIn(f"insert into public.{table}",lower);self.assertNotIn(f"delete from public.{table}",lower)

    def test_migration_enforces_event_and_execution_idempotency(self):
        lower=SQL.lower(); self.assertIn("transition_key text not null unique",lower);self.assertIn("contract-expired:%s:%s:%s:v1",lower)
        self.assertIn("on conflict(idempotency_key) do nothing",lower);self.assertIn("if found then",lower)

    def test_migration_dry_run_returns_before_audit_insert(self):
        lower=SQL.lower(); self.assertLess(lower.index("if v_dry then return v_result"),lower.index("insert into public.contract_transition_executions"))


if __name__=="__main__":unittest.main()
