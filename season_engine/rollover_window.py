from __future__ import annotations
from dataclasses import asdict,dataclass,field
from datetime import datetime,timedelta,timezone
from types import MappingProxyType
from typing import Any,Mapping,Sequence

from season_engine.rollover_service import RolloverAuthorityService,stable_fingerprint
from season_engine.contract_authority_preflight import ContractAuthorityPreflightService

POLICY_DEADLINE_RULE="SEVEN_CALENDAR_DAYS_AFTER_OFFICIAL_COMMISSIONER_ROLLOVER_NOTICE"
POLICY_FAILURE_OUTCOME="RELEASE_AT_ROLLOVER_TO_COMMISSIONER_HOLD"
EXPECTED_OWNER_CASES=108;EXPECTED_COMMISSIONER_CASES=13;VALIDATOR_VERSION="rollover-window-v1"

@dataclass(frozen=True)
class RolloverPreflightRequest:
    league_id:str;source_season:int;target_season:int;policy_id:str;expected_policy_fingerprint:str;requested_by:str;request_id:str;generated_at:datetime
@dataclass(frozen=True)
class OwnerPopulationCase:
    league_id:str;source_season:int;target_season:int;league_team_id:str;player_id:str;player_name:str;agreement_id:str;agreement_status:str;source_salary:str|None;source_contract_years:int;target_obligation_state:str;rostered_status:str;roster_slot:str|None;taxi:bool;ir:bool;rookie_status:str;waiver_status:str;contract_conflict_status:str;decision_eligible:bool;blockers:tuple[str,...];warnings:tuple[str,...];evidence_fingerprint:str;provenance:tuple[str,...]
@dataclass(frozen=True)
class OwnerPopulationResult:
    cases:tuple[OwnerPopulationCase,...];actual_count:int;expected_count:int;count_difference:int;blockers:tuple[str,...];warnings:tuple[str,...];fingerprint:str
@dataclass(frozen=True)
class CommissionerPreviewCase:
    player_id:str;player_name:str;agreement_id:str;league_team_id:str;review_type:str;preserved_facts:Mapping[str,Any];evidence_fingerprint:str
@dataclass(frozen=True)
class CommissionerPopulationPreview:
    cases:tuple[CommissionerPreviewCase,...];actual_count:int;expected_count:int;count_difference:int;blockers:tuple[str,...];fingerprint:str
@dataclass(frozen=True)
class RolloverPreflightResult:
    league_id:str;source_season:int;target_season:int;policy_id:str;policy_fingerprint:str;policy_valid:bool;authority_state:Mapping[str,Any];execution_control_state:Mapping[str,Any];contract_authority_state:Mapping[str,Any];historical_integrity_state:Mapping[str,Any];owner_population_preview:OwnerPopulationResult;commissioner_population_preview:CommissionerPopulationPreview;blockers:tuple[str,...];warnings:tuple[str,...];checks:Mapping[str,bool];preflight_fingerprint:str;execution_creation_eligible:bool;generated_at:datetime;provenance:tuple[str,...]
@dataclass(frozen=True)
class OwnerDecisionValidationResult:
    valid:bool;decision_id:str;player_id:str;choice:str;current_status:str;proposed_status:str;authorization_checks:Mapping[str,bool];deadline_checks:Mapping[str,bool];agreement_checks:Mapping[str,bool];team_checks:Mapping[str,bool];salary_checks:Mapping[str,str];term_checks:Mapping[str,str];cap_checks:Mapping[str,str];roster_checks:Mapping[str,str];conflict_checks:Mapping[str,str];blockers:tuple[str,...];warnings:tuple[str,...];evidence_fingerprint:str;validated_at:datetime;validator_version:str=VALIDATOR_VERSION

def canonical(value:Any)->Any:
    # Walk fields directly: dataclasses.asdict deep-copies MappingProxyType
    # evidence and fails before authenticated execution creation.
    if hasattr(value,"__dataclass_fields__"):return {k:canonical(getattr(value,k)) for k in value.__dataclass_fields__}
    if isinstance(value,Mapping):return {str(k):canonical(v) for k,v in sorted(value.items(),key=lambda x:str(x[0]))}
    if isinstance(value,(list,tuple,set)):return [canonical(v) for v in value]
    if isinstance(value,datetime):return value.astimezone(timezone.utc).isoformat()
    return value
def material_fingerprint(value:Any)->str:return stable_fingerprint(canonical(value))
def resolve_owner_deadline(notice:datetime)->datetime:
    if notice.tzinfo is None or notice.utcoffset() is None:raise ValueError("official notice timestamp must be timezone-aware")
    return notice.astimezone(timezone.utc)+timedelta(hours=168)

class OwnerPopulationBuilder:
    def build(self,league_id:str,source:int,target:int,report)->OwnerPopulationResult:
        cases=[];seen=set();blockers=[]
        for item in report.roster_exceptions:
            if item.classification!="ROSTERED_EXPIRED_POLICY_UNDEFINED":continue
            key=(item.agreement_id,item.player_id,item.team_id)
            if key in seen:blockers.append(f"duplicate_owner_case:{item.agreement_id}")
            seen.add(key);local=[]
            if not item.player_id:local.append("canonical_player_identity_unresolved")
            if not item.team_id:local.append("league_team_unresolved")
            slot=(item.taxi_or_ir or "").lower() or None;evidence={"league_id":league_id,"source_season":source,"target_season":target,"agreement_id":item.agreement_id,"player_id":item.player_id,"team_id":item.team_id,"roster_status":item.roster_status,"roster_slot":slot,"agreement_status":item.contract_status,"salary":item.evidence.get("salary"),"years":item.evidence.get("years_remaining"),"blockers":local}
            cases.append(OwnerPopulationCase(league_id,source,target,item.team_id,item.player_id,item.player_name,item.agreement_id,item.contract_status,item.evidence.get("salary"),int(item.evidence.get("years_remaining") or 0),"none_expired",item.roster_status,slot,slot=="taxi",slot=="ir","unknown","unknown","none",not local,tuple(local),(),material_fingerprint(evidence),("contract_agreements","contract_seasons","season_roster_assignments")))
        cases.sort(key=lambda x:(x.league_team_id,x.player_id,x.agreement_id));actual=len(cases)
        if any(x.blockers for x in cases):blockers.append("owner_population_contains_blocked_cases")
        if actual!=EXPECTED_OWNER_CASES:blockers.append(f"owner_population_count:{actual}:expected:{EXPECTED_OWNER_CASES}")
        basis=[{"agreement_id":x.agreement_id,"player_id":x.player_id,"team_id":x.league_team_id,"evidence_fingerprint":x.evidence_fingerprint} for x in cases]
        return OwnerPopulationResult(tuple(cases),actual,EXPECTED_OWNER_CASES,actual-EXPECTED_OWNER_CASES,tuple(dict.fromkeys(blockers)),(),material_fingerprint({"league":league_id,"source":source,"target":target,"cases":basis}))

class CommissionerPopulationBuilder:
    def build(self,report)->CommissionerPopulationPreview:
        cases=[]
        for item in report.roster_exceptions:
            if item.classification not in {"ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED","EXPIRED_UNROSTERED_PUBLICATION_PENDING"}:continue
            review="active_off_roster_liability" if item.classification.startswith("ACTIVE") else "expired_unrostered_publication_candidate"
            facts={"agreement_status":item.contract_status,"roster_status":item.roster_status,"salary":item.evidence.get("salary"),"publication_blocked":True,"acquisition_blocked":True,"second_agreement_blocked":review=="active_off_roster_liability","termination_inferred":False,"dead_cap_inferred":False}
            cases.append(CommissionerPreviewCase(item.player_id,item.player_name,item.agreement_id,item.team_id,review,MappingProxyType(facts),material_fingerprint({"player":item.player_id,"agreement":item.agreement_id,"review":review,"facts":facts})))
        cases.sort(key=lambda x:(x.review_type,x.player_id,x.agreement_id));actual=len(cases);blockers=() if actual==EXPECTED_COMMISSIONER_CASES else (f"commissioner_population_count:{actual}:expected:{EXPECTED_COMMISSIONER_CASES}",)
        return CommissionerPopulationPreview(tuple(cases),actual,EXPECTED_COMMISSIONER_CASES,actual-EXPECTED_COMMISSIONER_CASES,blockers,material_fingerprint([{"player":x.player_id,"agreement":x.agreement_id,"evidence":x.evidence_fingerprint} for x in cases]))

class RolloverPreflightService:
    def __init__(self,client):self.client=client
    def run(self,request:RolloverPreflightRequest)->RolloverPreflightResult:
        seasons=self.client.table("league_seasons").select("*").eq("league_id",request.league_id).execute().data or []
        policies=self.client.table("league_rollover_policies").select("*").eq("id",request.policy_id).execute().data or []
        executions=self.client.table("rollover_executions").select("*").eq("league_id",request.league_id).eq("source_season",request.source_season).eq("target_season",request.target_season).execute().data or []
        history=self.client.table("historical_capture_executions").select("*").execute().data or []
        report=RolloverAuthorityService(self.client).build_rollover_readiness_report(request.league_id);owner=OwnerPopulationBuilder().build(request.league_id,request.source_season,request.target_season,report);commissioner=CommissionerPopulationBuilder().build(report);contract=ContractAuthorityPreflightService(self.client).run(request.league_id,request.source_season,request.target_season)
        policy=policies[0] if len(policies)==1 else {};metadata=policy.get("metadata") or {};stored_payload=metadata.get("policy_payload") or {};recalculated=(policy.get("fingerprint") if metadata.get("fingerprint_algorithm")=="postgres-jsonb-v1" else stable_fingerprint({k:v for k,v in stored_payload.items() if k!="fingerprint"})) if stored_payload else None
        source=[x for x in seasons if int(x.get("season") or 0)==request.source_season];target=[x for x in seasons if int(x.get("season") or 0)==request.target_season]
        checks={"league_exists":bool(seasons),"source_exists":len(source)==1,"target_exists":len(target)==1,"source_active":bool(source and source[0].get("is_active") and source[0].get("status")=="active"),"target_scheduled":bool(target and not target[0].get("is_active") and target[0].get("status")=="scheduled"),"sequential_boundary":request.target_season==request.source_season+1,"policy_unique":len(policies)==1,"policy_boundary":bool(policy and str(policy.get("league_id"))==request.league_id and int(policy.get("source_season") or 0)==request.source_season and int(policy.get("target_season") or 0)==request.target_season),"policy_fingerprint":bool(policy and policy.get("fingerprint")==request.expected_policy_fingerprint==recalculated),"policy_approved_inactive":bool(policy and policy.get("status")=="approved" and policy.get("effective_at") is None),"no_existing_execution":not any(x.get("status")!="cancelled" for x in executions),"contract_authority_target":contract.ready,"historical_integrity":bool(history and all(x.get("status") in {"validated","finalized"} for x in history)),"owner_population_complete":not owner.blockers,"commissioner_population_complete":not commissioner.blockers,"execution_schema_available":True,"authorities_uninitialized":self._authority_empty(request.league_id,request.target_season)}
        blockers=tuple(k for k,v in checks.items() if not v)+contract.blockers+owner.blockers+commissioner.blockers
        basis={"request":{"league_id":request.league_id,"source":request.source_season,"target":request.target_season,"policy_id":request.policy_id,"policy_fingerprint":request.expected_policy_fingerprint},"checks":checks,"owner_fingerprint":owner.fingerprint,"commissioner_fingerprint":commissioner.fingerprint,"contract_source":report.source_fingerprint,"contract_preflight_fingerprint":contract.fingerprint,"history":[{"id":x.get("id"),"fingerprint":x.get("source_fingerprint"),"status":x.get("status")} for x in history],"executions":[{"id":x.get("id"),"status":x.get("status")} for x in executions]}
        fp=material_fingerprint(basis)
        return RolloverPreflightResult(request.league_id,request.source_season,request.target_season,request.policy_id,request.expected_policy_fingerprint,all(checks[k] for k in ("policy_unique","policy_boundary","policy_fingerprint","policy_approved_inactive")),MappingProxyType({"league":request.source_season,"publication":"uninitialized","dead_cap":"uninitialized","cap":"uninitialized"}),MappingProxyType({"existing":len(executions)}),MappingProxyType({"season":report.context.contract_operational_season,"source_fingerprint":report.source_fingerprint}),MappingProxyType({"capture_count":len(history),"valid":checks["historical_integrity"]}),owner,commissioner,tuple(dict.fromkeys(blockers)),tuple(report.warnings),MappingProxyType(checks),fp,not blockers,request.generated_at,("league_seasons","league_rollover_policies","normalized contracts","season_roster_assignments","historical capture","rollover control"))
    def _authority_empty(self,league,season):
        for table in ("free_agent_publications","dead_cap_obligations","dead_cap_season_authorities","cap_season_authorities"):
            rows=self.client.table(table).select("id").eq("league_id",league).eq("season",season).execute().data or []
            if rows:return False
        return True

class OwnerAuthorizationService:
    def authorize(self,*,user_id,league_id,decision_team_id,memberships,execution_status,deadline,now,locked=False,executed=False):
        matches=[x for x in memberships if str(x.get("user_id"))==str(user_id) and str(x.get("league_id"))==str(league_id)]
        if len(matches)!=1:return {"authorized":False,"reason":"membership_not_unique"}
        m=matches[0];role=str(m.get("role") or "").lower();team=str(m.get("league_team_id") or "")
        admin=role in {"commissioner","admin","host"};owner=role in {"owner","co_owner","co-owner"} and team==str(decision_team_id)
        ok=bool(user_id) and (admin or owner) and execution_status=="decision_window_open" and now<deadline and not locked and not executed
        return {"authorized":ok,"reason":None if ok else "owner_authorization_failed","resolved_team_id":team or None,"commissioner":admin}

class OwnerDecisionValidator:
    def validate(self,decision,choice,*,authorization,now,recontract_agreement=None,recontract_event=None):
        blockers=[];warnings=[];deadline=decision.get("deadline")
        if not authorization.get("authorized"):blockers.append("unauthorized")
        if not isinstance(deadline,datetime) or now>=deadline:blockers.append("deadline_closed")
        if decision.get("locked_at"):blockers.append("decision_locked")
        if decision.get("execution_status") in {"executing","executed","cancelled"}:blockers.append("decision_not_mutable")
        proposed={"recontract":"recontract_submitted","decline":"decline_submitted","commissioner_review":"commissioner_review_requested"}.get(choice)
        if not proposed:blockers.append("invalid_choice")
        agreement_checks={"normalized_reference_present":bool(recontract_agreement and recontract_event) if choice=="recontract" else True}
        if not agreement_checks["normalized_reference_present"]:blockers.append("recontract_references_required")
        if choice!="recontract" and (recontract_agreement or recontract_event):blockers.append("non_recontract_references_forbidden")
        evidence={"decision":decision.get("id"),"player":decision.get("player_id"),"choice":choice,"current":decision.get("decision_status"),"proposed":proposed,"agreement":getattr(recontract_agreement,"id",None) if recontract_agreement else None,"event":getattr(recontract_event,"id",None) if recontract_event else None,"blockers":blockers}
        deferred="deferred_to_authority_preparation_and_final_plan"
        return OwnerDecisionValidationResult(not blockers,str(decision.get("id")),str(decision.get("player_id")),choice,str(decision.get("decision_status")),proposed or "invalid",MappingProxyType(dict(authorization)),MappingProxyType({"before_deadline":"deadline_closed" not in blockers}),MappingProxyType(agreement_checks),MappingProxyType({"same_team":authorization.get("authorized",False)}),MappingProxyType({"status":deferred}),MappingProxyType({"status":deferred}),MappingProxyType({"status":deferred}),MappingProxyType({"status":"validated_source_roster_only"}),MappingProxyType({"status":"preliminary_only"}),tuple(blockers),tuple(warnings),material_fingerprint(evidence),now)

class RolloverWindowService:
    """Authenticated-session facade; actor identity is resolved by auth.uid() in SQL."""
    def __init__(self,client):self.client=client
    def create_execution_as_commissioner(self,payload):return self.client.rpc("create_rollover_execution_authenticated",{"p_request":payload}).execute().data
    def open_notice_window_as_commissioner(self,payload):return self.client.rpc("open_rollover_notice_window_authenticated",{"p_request":payload}).execute().data
    def submit_owner_decision_as_authenticated_user(self,payload):return self.client.rpc("submit_rollover_owner_decision_authenticated",{"p_request":payload}).execute().data
    def override_owner_decision_as_commissioner(self,payload):return self.client.rpc("override_rollover_owner_decision_authenticated",{"p_request":payload}).execute().data
    def close_window_as_commissioner(self,payload):return self.client.rpc("close_rollover_decision_window_authenticated",{"p_request":payload}).execute().data
    def cancel_execution_as_commissioner(self,payload):return self.client.rpc("cancel_rollover_execution_authenticated",{"p_request":payload}).execute().data

def rollover_window_readiness(execution:Mapping[str,Any]|None,owner_decisions:Sequence[Mapping[str,Any]]=(),commissioner_reviews:Sequence[Mapping[str,Any]]=()):
    if not execution:return {"status":"execution_control_ready","blockers":("rollover execution not created",)}
    status=execution.get("status")
    if status in {"draft","preflight_ready"}:return {"status":"notice_window_required","blockers":("official notice not recorded",)}
    if status in {"notice_open","decision_window_open"}:return {"status":"decision_window_open","blockers":()}
    unresolved=[x for x in owner_decisions if x.get("decision_status") in {"blocked","recontract_invalid","commissioner_review_requested"}]
    if status=="decision_window_closed" and unresolved:return {"status":"decision_resolution_required","blockers":tuple(str(x.get("id")) for x in unresolved)}
    if status=="decision_window_closed":return {"status":"commissioner_review_required","blockers":tuple(str(x.get("id")) for x in commissioner_reviews)}
    return {"status":"blocked","blockers":("unsupported execution state",)}
