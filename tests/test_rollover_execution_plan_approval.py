from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import re
import threading
import unittest

from season_engine.execution_approval import (
    APPROVAL_STATEMENT_CODE, RolloverExecutionApprovalService, build_approval_input,
    execution_approval_readiness,
)
from season_engine.execution_plan import RolloverExecutionPlanner, TrustedExecutionPlanService
from tests.test_rollover_execution_plan import artifacts


class Response:
    def __init__(self, data): self.data = data
class Call:
    def __init__(self, data): self.data = data
    def execute(self): return Response(self.data)
class Client:
    def __init__(self, data): self.data=data;self.calls=[]
    def rpc(self,name,args):self.calls.append((name,args));return Call(self.data)


def evidence():
    value,result,validation=artifacts()
    plan,_=RolloverExecutionPlanner().build_plan(value,result,validation,generated_at=datetime(2026,1,1,tzinfo=timezone.utc))
    p=dict(TrustedExecutionPlanService.serialize_plan(plan))
    s={"id":result.id,"rollover_execution_id":result.execution_id,"simulation_version":result.simulation_version,
       "simulation_status":"valid","valid":True,"executable":True,"plan_eligible":True,"blockers":[],
       "input_fingerprint":result.input_fingerprint,"result_fingerprint":result.result_fingerprint,
       "preflight_fingerprint":result.preflight_fingerprint,"policy_fingerprint":result.policy_fingerprint,
       "owner_population_fingerprint":result.owner_population_fingerprint,
       "commissioner_population_fingerprint":result.commissioner_population_fingerprint,
       "authority_preparation_fingerprint":result.authority_preparation_fingerprint}
    return p,s


class ApprovalDomainTests(unittest.TestCase):
    def setUp(self):
        self.plan,self.sim=evidence();self.value=build_approval_input(self.plan,approval_statement="I approve this exact immutable plan.",idempotency_key="approve-1")
    def test_deterministic_fingerprint_and_exclusions(self):
        service=RolloverExecutionApprovalService();a=service.build_approval(self.value,"actor",datetime(2026,1,1,tzinfo=timezone.utc));b=service.build_approval(self.value,"actor-two",datetime(2026,2,1,tzinfo=timezone.utc))
        self.assertEqual(a.approval_fingerprint,b.approval_fingerprint);self.assertEqual(a.id,b.id)
        serialized=service.serialize(a);self.assertIn("approved_at",serialized);self.assertEqual(serialized["approval_statement_code"],APPROVAL_STATEMENT_CODE)
    def test_plan_and_simulation_identity(self):
        RolloverExecutionApprovalService.validate_approval(self.value,self.plan,self.sim)
        for key in ("plan_fingerprint","plan_input_fingerprint","policy_fingerprint","preflight_fingerprint","owner_population_fingerprint","commissioner_population_fingerprint","authority_preparation_fingerprint"):
            bad=dict(self.plan);bad[key]="f"*64
            with self.assertRaisesRegex(ValueError,"stale plan"):RolloverExecutionApprovalService.validate_approval(self.value,bad,self.sim)
        bad=dict(self.sim);bad["result_fingerprint"]="f"*64
        with self.assertRaisesRegex(ValueError,"stale simulation"):RolloverExecutionApprovalService.validate_approval(self.value,self.plan,bad)
    def test_statement_and_eligibility(self):
        with self.assertRaisesRegex(ValueError,"nonblank"):RolloverExecutionApprovalService.validate_approval(replace(self.value,approval_statement=" "),self.plan,self.sim)
        with self.assertRaisesRegex(ValueError,"statement"):RolloverExecutionApprovalService.validate_approval(replace(self.value,approval_statement_code="WRONG"),self.plan,self.sim)
        bad=dict(self.plan);bad["blockers"]=["blocked"]
        with self.assertRaisesRegex(ValueError,"not approval eligible"):RolloverExecutionApprovalService.validate_approval(self.value,bad,self.sim)
    def test_operation_sequence_and_count(self):
        bad=replace(self.value,operation_fingerprints=tuple(reversed(self.value.operation_fingerprints)))
        with self.assertRaisesRegex(ValueError,"operation fingerprints"):RolloverExecutionApprovalService.validate_approval(bad,self.plan,self.sim)
    def test_durable_lock(self):
        approval=RolloverExecutionApprovalService.build_approval(self.value,"actor")
        lock=RolloverExecutionApprovalService.build_lock(approval)
        self.assertEqual(lock.lock_type,"cutover");self.assertEqual(lock.lock_status,"active");self.assertEqual(lock.approval_id,approval.id)
    def test_no_domain_execution(self):
        approval=RolloverExecutionApprovalService.build_approval(self.value,"actor")
        self.assertEqual(approval.operation_count,len(self.plan["ordered_operations"]));self.assertFalse(any(hasattr(RolloverExecutionApprovalService,name) for name in ("execute_plan","apply_operations","rollover")))


class ApprovalTransportTests(unittest.TestCase):
    def test_approve_rejects_caller_conclusions_and_maps_rpc(self):
        service=RolloverExecutionApprovalService(Client({"approval":{"id":"a"},"lock":{"id":"l"}}))
        with self.assertRaisesRegex(ValueError,"forbidden"):service.approve({"approval_status":"approved"})
        result=service.approve({"idempotency_key":"k"});self.assertEqual(result["approval"]["id"],"a")
        self.assertEqual(service.client.calls[0][0],"approve_rollover_execution_plan_authenticated")
    def test_revoke_mapping_and_malformed_results(self):
        service=RolloverExecutionApprovalService(Client({"approval":{"id":"a","approval_status":"revoked"},"lock":{"status":"released"}}))
        self.assertEqual(service.revoke({"reason":"changed","idempotency_key":"r"})["approval"]["approval_status"],"revoked")
        with self.assertRaisesRegex(ValueError,"malformed"):RolloverExecutionApprovalService(Client({})).approve({})


class ReadinessTests(unittest.TestCase):
    def test_readiness_states(self):
        self.assertEqual(execution_approval_readiness(None,None,None,None)["status"],"execution_control_ready")
        self.assertEqual(execution_approval_readiness({"id":"e"},{"id":"p"},None,None)["status"],"execution_plan_ready")
        plan={"id":"p","plan_status":"approved_for_execution","approved_for_execution":True,"plan_fingerprint":"f","simulation_result_fingerprint":"s"}
        approval={"id":"a","approval_status":"approved","simulation_result_fingerprint":"s"}
        lock={"approval_id":"a","execution_plan_id":"p","lock_status":"active","plan_fingerprint":"f"}
        self.assertEqual(execution_approval_readiness({"id":"e"},plan,approval,lock)["status"],"cutover_locked")
        self.assertEqual(execution_approval_readiness({"id":"e"},plan,approval,None)["status"],"execution_plan_approval_stale")
        self.assertEqual(execution_approval_readiness({"id":"e"},plan,{"approval_status":"revoked"},None)["status"],"execution_plan_approval_revoked")


class InMemoryApprovalBoundary:
    """Transactional test double for approval lifecycle invariants, not domain execution."""
    def __init__(self):
        self.mutex=threading.Lock();self.execution_status="authority_ready";self.plan_status="valid"
        self.approval=None;self.lock=None;self.requests={};self.domain={"contracts":211,"rosters":10,"seasons":2}
    def _replay(self,key,material):
        if key in self.requests:
            prior_material,result=self.requests[key]
            if prior_material!=material:raise ValueError("idempotency conflict")
            return result
    def approve(self,key="approve",material="same",authorized=True,league="league",plan_valid=True):
        with self.mutex:
            replay=self._replay(key,material)
            if replay:return replay
            if not authorized:raise PermissionError("commissioner required")
            if league!="league":raise PermissionError("cross-league")
            if self.execution_status!="authority_ready":raise ValueError("execution state")
            if self.plan_status!="valid" or not plan_valid:raise ValueError("invalid plan")
            if self.approval or self.lock:raise ValueError("already approved")
            before=dict(self.domain);self.approval={"status":"approved"};self.lock={"status":"active"}
            self.plan_status="approved_for_execution";self.execution_status="execution_ready"
            result={"approval":self.approval,"lock":self.lock,"operations_executed":0}
            assert self.domain==before;self.requests[key]=(material,result);return result
    def revoke(self,key="revoke",material="same"):
        with self.mutex:
            replay=self._replay(key,material)
            if replay:return replay
            if self.execution_status!="execution_ready":raise ValueError("execution started")
            if not self.approval or not self.lock:raise ValueError("approval missing")
            before=dict(self.domain);self.approval={"status":"revoked"};self.lock={"status":"released"}
            self.plan_status="valid";self.execution_status="authority_ready"
            result={"approval":self.approval,"lock":self.lock,"operations_executed":0}
            assert self.domain==before;self.requests[key]=(material,result);return result


class LifecycleInvariantTests(unittest.TestCase):
    def test_success_replay_conflict_and_zero_domain_mutation(self):
        h=InMemoryApprovalBoundary();before=dict(h.domain);first=h.approve();self.assertEqual(first["operations_executed"],0)
        self.assertIs(first,h.approve());self.assertEqual(h.domain,before)
        with self.assertRaisesRegex(ValueError,"conflict"):h.approve(material="changed")
        revoked=h.revoke();self.assertEqual(revoked["operations_executed"],0);self.assertEqual(h.domain,before)
        self.assertIs(revoked,h.revoke())
        with self.assertRaisesRegex(ValueError,"conflict"):h.revoke(material="changed")
    def test_invalid_duplicate_started_auth_and_cross_league_rejections(self):
        with self.assertRaisesRegex(ValueError,"invalid plan"):InMemoryApprovalBoundary().approve(plan_valid=False)
        h=InMemoryApprovalBoundary();h.approve()
        with self.assertRaisesRegex(ValueError,"execution state"):h.approve(key="second")
        h.execution_status="executing"
        with self.assertRaisesRegex(ValueError,"execution started"):h.revoke()
        with self.assertRaises(PermissionError):InMemoryApprovalBoundary().approve(authorized=False)
        with self.assertRaisesRegex(PermissionError,"cross-league"):InMemoryApprovalBoundary().approve(league="other")
    def test_concurrent_approvals_only_one_succeeds(self):
        h=InMemoryApprovalBoundary();out=[]
        def attempt(key):
            try:out.append((key,"ok",h.approve(key=key)))
            except Exception as exc:out.append((key,type(exc).__name__,str(exc)))
        threads=[threading.Thread(target=attempt,args=(f"k{i}",)) for i in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(x[1]=="ok" for x in out),1);self.assertEqual(h.lock["status"],"active")


class SqlArtifactAuditTests(unittest.TestCase):
    ROOT=Path(__file__).resolve().parents[1]
    H=(ROOT/"supabase/migrations/20260813_rollover_execution_plan_approval.sql").read_text()
    HARDENING=(ROOT/"supabase/migrations/20260814_rollover_execution_plan_approval_catalog_hardening.sql").read_text()
    IMMUTABILITY_FIX=(ROOT/"supabase/migrations/20260815_rollover_execution_plan_immutability_fix.sql").read_text()
    VERIFY=(ROOT/"supabase/verification/verify_rollover_execution_plan_approval.sql").read_text()
    def test_approval_revocation_locking_and_atomicity_guards(self):
        for token in ("require_authenticated_user","require_commissioner_authority","pg_advisory_xact_lock","for update","rollover_operation_retry","record_rollover_operation","operations_executed',0"):
            self.assertIn(token,self.H.lower())
        self.assertIn("approval cannot be revoked after execution start",self.H)
        self.assertIn("status='released'",self.H)
    def test_hardening_expected_state_and_delete_guards(self):
        for token in ("expected_execution_status","expected_plan_status","stale execution status","stale execution plan status","before delete","durable cutover locks cannot be deleted"):
            self.assertIn(token,self.HARDENING)
        self.assertIn("revoke all on function public.approve_rollover_execution_plan_authenticated_phase3b5h_base",self.HARDENING)
    def test_verification_is_select_only(self):
        stripped=re.sub(r"--.*?$|/\*.*?\*/","",self.VERIFY,flags=re.M|re.S)
        self.assertIsNone(re.search(r"(?im)^\s*(insert|update|delete|alter|create|drop|grant|revoke|call|do|begin|commit|rollback)\b",stripped))
        self.assertNotRegex(stripped, r"(?i)from\s+pg_proc\s+\w+\s*,\s*cross\s+join")
        self.assertNotIn("cross join lateral",stripped.lower())
        self.assertNotIn("aclexplode",stripped.lower())
        self.assertIn("with checks(check_name,expected_state,observed_state,passes,notes) as (",stripped)
        self.assertRegex(stripped, r"(?is)with checks\s*\([^)]*\)\s+as\s*\(\s*values.+\)\s*select\s+'16_invariant_summary'")
        self.assertIn("16_invariant_summary",self.VERIFY)
    def test_additive_plan_immutability_fix_uses_real_row_shape(self):
        sql=self.IMMUTABILITY_FIX.lower()
        self.assertNotIn("new.created_at",sql)
        self.assertNotIn("old.created_at",sql)
        self.assertIn("to_jsonb(new)-array[",sql)
        self.assertIn("is distinct from",sql)
        self.assertIn("'approved_for_execution'",sql)
        self.assertIn("before update or delete",sql)
        self.assertIn("execution plans cannot be deleted",sql)
        self.assertIn("revoke approval before changing approved plan",sql)

if __name__=="__main__":unittest.main()
