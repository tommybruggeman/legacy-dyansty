from __future__ import annotations

import argparse, json
from datetime import datetime, timezone

from .transition_execution_models import ContractTransitionExecutionPreview, ContractTransitionExecutionRequest, EXECUTOR_VERSION, REQUEST_VERSION
from .transition_execution_validator import APPROVED_2025_2026_COUNTS, validate_contract_transition_execution
from .transition_service import ContractTransitionService


class ContractTransitionRetryError(RuntimeError):
    def __init__(self,code,message,details=None):
        self.code,self.details=code,details or {}; super().__init__(f"{code}: {message}")


def build_contract_transition_execution_request(*, plan, dry_run, expected_source_fingerprint, expected_plan_fingerprint, requested_by=None):
    if not isinstance(dry_run,bool): raise ValueError("dry_run must be supplied explicitly as true or false")
    return ContractTransitionExecutionRequest(
        league_id=plan.request.league_id,source_season=plan.request.source_season,target_season=plan.request.target_season,
        source_league_season_id=plan.request.source_league_season_id,target_league_season_id=plan.request.target_league_season_id,
        expected_source_fingerprint=expected_source_fingerprint,actual_source_fingerprint=plan.source_fingerprint,
        expected_plan_fingerprint=expected_plan_fingerprint,actual_plan_fingerprint=plan.plan_fingerprint,
        transition_key=f"contract-transition:{plan.request.league_id}:{plan.request.source_season}:{plan.request.target_season}:v1",
        request_version=REQUEST_VERSION,planner_version=plan.request.planner_version,executor_version=EXECUTOR_VERSION,
        dry_run=dry_run,requested_at=datetime.now(timezone.utc).isoformat(),expected_counts=dict(APPROVED_2025_2026_COUNTS),
        agreement_plan=tuple({k:x.get(k) for k in ("agreement_id","player_id","league_team_id","outcome","source_salary","target_salary")} for x in plan.classifications),
        requested_by=requested_by)


class ContractTransitionExecutionService:
    def __init__(self,client): self.client=client
    def preview(self,league_id,source_season,target_season,*,expected_source_fingerprint,expected_plan_fingerprint,dry_run,**_):
        plan=ContractTransitionService(self.client).plan(league_id,source_season,target_season,expected_source_fingerprint)
        request=build_contract_transition_execution_request(plan=plan,dry_run=dry_run,
            expected_source_fingerprint=expected_source_fingerprint,expected_plan_fingerprint=expected_plan_fingerprint)
        validation=validate_contract_transition_execution(request,plan)
        return ContractTransitionExecutionPreview(validation["safe_to_apply"],request,
            {"satisfy_2025":plan.counts["planned_satisfied"],"activate_2026":plan.counts["planned_active"],
             "expire_agreements":plan.counts["planned_expired_agreements"],"create_expiration_events":plan.counts["planned_expiration_events"]},
            {"agreements_total":plan.counts["agreements"],"agreements_active":plan.counts["continues"],"agreements_expired":plan.counts["expires"],
             "obligations_2025_satisfied":plan.counts["source_obligations"],"obligations_2026_active":plan.counts["target_obligations"],
             "obligations_2027_scheduled":plan.counts["season_2027_obligations"],"events_total":211+plan.counts["planned_expiration_events"]},
            tuple(validation["warnings"]),tuple(validation["blocking_errors"]))
    def dry_run(self,*args,**kwargs):
        preview=self.preview(*args,**kwargs,dry_run=True)
        if not preview.safe_to_apply:return {"safe_to_apply":False,"blocking_errors":preview.blocking_errors}
        return self.client.rpc("apply_contract_transition",{"p_request":preview.request.payload()}).execute().data
    def apply(self,*args,confirm_apply=False,**kwargs):
        if confirm_apply is not True: raise ValueError("apply requires confirm_apply=True")
        identity=self._basic_identity(*args,**kwargs)
        rows=self._execution_rows(identity["transition_key"])
        if len(rows)>1: raise ContractTransitionRetryError("duplicate_execution","More than one execution exists for the transition key.")
        if rows:return self._validated_retry(identity,rows[0])
        preview=self.preview(*args,**kwargs,dry_run=False)
        if not preview.safe_to_apply: raise ValueError(f"Contract transition blocked: {preview.blocking_errors}")
        return self.client.rpc("apply_contract_transition",{"p_request":preview.request.payload()}).execute().data
    def get(self,transition_key):
        rows=self._execution_rows(transition_key)
        return rows[0] if len(rows)==1 else None
    def _execution_rows(self,transition_key):
        return self.client.table("contract_transition_executions").select("*").eq("transition_key",transition_key).execute().data or []
    def _basic_identity(self,league_id,source_season,target_season,*,expected_source_fingerprint,expected_plan_fingerprint,
            request_version=REQUEST_VERSION,planner_version="contract-transition-v1",executor_version=EXECUTOR_VERSION,**_):
        if not league_id or source_season!=2025 or target_season!=2026 or target_season!=source_season+1:
            raise ContractTransitionRetryError("invalid_request","Approved execution identity must be league-scoped 2025 -> 2026.")
        key=f"contract-transition:{league_id}:{source_season}:{target_season}:v1"
        if request_version!=REQUEST_VERSION or planner_version!="contract-transition-v1" or executor_version!=EXECUTOR_VERSION:
            raise ContractTransitionRetryError("invalid_version","Unsupported retry request version.")
        seasons=self.client.table("league_seasons").select("id,league_id,season").eq("league_id",league_id).execute().data or []
        source=[x for x in seasons if int(x.get("season") or 0)==source_season]; target=[x for x in seasons if int(x.get("season") or 0)==target_season]
        if len(source)!=1 or len(target)!=1:raise ContractTransitionRetryError("season_identity","Cannot resolve one source and target season.")
        return {"league_id":league_id,"source_season":source_season,"target_season":target_season,"source_league_season_id":str(source[0]["id"]),
            "target_league_season_id":str(target[0]["id"]),"transition_key":key,"expected_source_fingerprint":expected_source_fingerprint,
            "expected_plan_fingerprint":expected_plan_fingerprint,"request_version":request_version,"planner_version":planner_version,"executor_version":executor_version}
    def _validated_retry(self,identity,row):
        if row.get("status") in {"applying","failed"}:
            raise ContractTransitionRetryError(f"execution_{row.get('status')}","Existing execution is not safely retryable.",{"execution_id":row.get("id")})
        if row.get("status")!="validated":raise ContractTransitionRetryError("execution_state","Unsupported existing execution status.")
        stored={"league_id":str(row.get("league_id")),"source_season":int(row.get("source_season")),"target_season":int(row.get("target_season")),
            "source_league_season_id":str(row.get("source_league_season_id")),"target_league_season_id":str(row.get("target_league_season_id")),
            "transition_key":row.get("transition_key"),"expected_source_fingerprint":row.get("expected_source_fingerprint"),
            "expected_plan_fingerprint":row.get("plan_fingerprint"),"request_version":row.get("request_version"),
            "planner_version":row.get("planner_version"),"executor_version":row.get("executor_version")}
        conflicts={key:{"requested":identity[key],"stored":value} for key,value in stored.items() if identity.get(key)!=value}
        if conflicts:raise ContractTransitionRetryError("conflicting_retry","Retry identity differs from the validated execution.",conflicts)
        self._validate_persisted_post_state(identity,row)
        result=dict(row.get("result") or {}); result.update({"status":"validated","idempotent":True,"execution_id":str(row["id"])})
        return result
    def _validate_persisted_post_state(self,identity,row):
        lid=identity["league_id"]; key=identity["transition_key"]
        agreements=self.client.table("contract_agreements").select("id,status").eq("league_id",lid).execute().data or []
        seasons=self.client.table("contract_seasons").select("id,season,obligation_status").eq("league_id",lid).execute().data or []
        events=self.client.table("contract_events").select("id,event_type,metadata").eq("league_id",lid).execute().data or []
        actual={"agreements":len(agreements),"active_agreements":sum(x.get("status")=="active" for x in agreements),
            "expired_agreements":sum(x.get("status")=="expired" for x in agreements),
            "satisfied_2025":sum(int(x.get("season") or 0)==2025 and x.get("obligation_status")=="satisfied" for x in seasons),
            "active_2026":sum(int(x.get("season") or 0)==2026 and x.get("obligation_status")=="active" for x in seasons),
            "scheduled_2027":sum(int(x.get("season") or 0)==2027 and x.get("obligation_status")=="scheduled" for x in seasons),
            "expired_events":sum(x.get("event_type")=="expired" and (x.get("metadata") or {}).get("transition_key")==key for x in events),
            "contract_events":len(events)}
        expected=dict((row.get("result") or {}).get("persisted") or {})
        required={"agreements":211,"active_agreements":92,"expired_agreements":119,"satisfied_2025":211,"active_2026":92,
            "scheduled_2027":32,"expired_events":119,"contract_events":330}
        expected={key:expected.get(key,required[key]) for key in required}
        execution_count=len(self._execution_rows(key)); actual["execution_rows"]=execution_count; expected["execution_rows"]=1
        drift={name:{"expected":expected[name],"actual":actual[name]} for name in expected if actual.get(name)!=expected[name]}
        if drift:raise ContractTransitionRetryError("persisted_state_drift","Validated execution result no longer matches normalized state.",drift)


def dry_run_contract_transition_execution(client,*args,**kwargs):return ContractTransitionExecutionService(client).dry_run(*args,**kwargs)
def apply_contract_transition(client,*args,**kwargs):return ContractTransitionExecutionService(client).apply(*args,**kwargs)
def get_contract_transition_execution(client,transition_key):return ContractTransitionExecutionService(client).get(transition_key)
def get_contract_transition_execution_status(client,transition_key):
    row=get_contract_transition_execution(client,transition_key); return row.get("status") if row else None


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("command",choices=["dry-run","apply"]); parser.add_argument("--league-id",required=True)
    parser.add_argument("--source-season",type=int,required=True); parser.add_argument("--target-season",type=int,required=True)
    parser.add_argument("--expected-source-fingerprint",required=True); parser.add_argument("--expected-plan-fingerprint",required=True)
    parser.add_argument("--confirm-apply",action="store_true"); args=parser.parse_args()
    if args.command=="apply" and not args.confirm_apply: parser.error("apply requires --confirm-apply")
    from auth import service_client
    service=ContractTransitionExecutionService(service_client()); kw=dict(league_id=args.league_id,source_season=args.source_season,target_season=args.target_season,
        expected_source_fingerprint=args.expected_source_fingerprint,expected_plan_fingerprint=args.expected_plan_fingerprint)
    result=service.dry_run(**kw) if args.command=="dry-run" else service.apply(**kw,confirm_apply=True)
    print(json.dumps(result,indent=2,default=str)); return 0


if __name__=="__main__":raise SystemExit(main())
