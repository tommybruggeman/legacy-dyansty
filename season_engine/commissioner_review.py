from __future__ import annotations

from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
from types import MappingProxyType
from typing import Any,Mapping,Sequence

from season_engine.rollover_service import RolloverAuthorityService,stable_fingerprint

EXPECTED_COMMISSIONER_CASES=13
VALIDATOR_VERSION="commissioner-review-v1"

OUTCOME_MATRIX:Mapping[str,frozenset[str]]=MappingProxyType({
 "active_off_roster_liability":frozenset({"preserve_active_liability","approve_termination","reject_termination","retain_contract","require_contract_resolution","blocked","cancelled"}),
 "expired_unrostered_publication_candidate":frozenset({"approve_publication","reject_publication","require_identity_resolution","require_contract_resolution","require_waiver_resolution","require_rookie_draft_resolution","blocked","cancelled"}),
 "owner_escalation":frozenset({"retain_contract","approve_termination","reject_termination","approve_dead_cap","reject_dead_cap","return_to_owner","require_contract_resolution","blocked","cancelled"}),
 "identity_conflict":frozenset({"require_identity_resolution","blocked","cancelled"}),
 "waiver_conflict":frozenset({"require_waiver_resolution","blocked","cancelled"}),
 "rookie_draft_conflict":frozenset({"require_rookie_draft_resolution","blocked","cancelled"}),
 "contract_conflict":frozenset({"require_contract_resolution","retain_contract","approve_termination","reject_termination","approve_dead_cap","reject_dead_cap","blocked","cancelled"}),
})

LEGAL_TRANSITIONS:Mapping[str,frozenset[str]]=MappingProxyType({
 "pending":frozenset({"under_review","cancelled"}),
 "under_review":frozenset({"evidence_required","decision_ready","blocked","cancelled"}),
 "evidence_required":frozenset({"under_review","blocked","cancelled"}),
 "decision_ready":frozenset({"approved","rejected","blocked","cancelled"}),
 "approved":frozenset({"superseded","executed"}),
 "rejected":frozenset({"superseded","executed"}),
 "superseded":frozenset({"under_review","cancelled"}),
 "blocked":frozenset({"under_review","cancelled"}),
 "cancelled":frozenset(),"executed":frozenset(),
})

@dataclass(frozen=True)
class CommissionerReviewCandidate:
 league_id:str;source_season:int;target_season:int;player_id:str;canonical_player_name:str;league_team_id:str;agreement_id:str;contract_event_id:str|None;review_type:str;agreement_status:str;roster_status:str;source_salary:str|None;source_contract_years:int;target_contract_state:str;publication_status:str;acquisition_status:str;second_agreement_status:str;dead_cap_status:str;evidence:Mapping[str,Any];blockers:tuple[str,...];warnings:tuple[str,...];evidence_fingerprint:str;provenance:tuple[str,...]

@dataclass(frozen=True)
class CommissionerPopulation:
 cases:tuple[CommissionerReviewCandidate,...];actual_count:int;expected_count:int;difference:int;blockers:tuple[str,...];warnings:tuple[str,...];fingerprint:str

@dataclass(frozen=True)
class CommissionerReviewDecision:
 review_id:str;execution_id:str;review_type:str;current_status:str;outcome:str|None;actor_user_id:str|None;reason:str|None;evidence:Mapping[str,Any];termination_event_id:str|None;dead_cap_event_id:str|None;publication_reference:str|None;retained_agreement_id:str|None;blockers:tuple[str,...];warnings:tuple[str,...];revision_number:int;request_fingerprint:str|None;decision_fingerprint:str;created_at:datetime|None;updated_at:datetime|None

@dataclass(frozen=True)
class CommissionerReviewValidationResult:
 valid:bool;valid_for_plan:bool;review_id:str;review_type:str;proposed_outcome:str;state_checks:Mapping[str,bool];identity_checks:Mapping[str,bool];contract_checks:Mapping[str,bool];roster_checks:Mapping[str,bool];termination_checks:Mapping[str,bool];publication_checks:Mapping[str,bool];dead_cap_checks:Mapping[str,bool];acquisition_checks:Mapping[str,bool];duplicate_agreement_checks:Mapping[str,bool];authority_checks:Mapping[str,str];blockers:tuple[str,...];warnings:tuple[str,...];evidence_fingerprint:str;validation_fingerprint:str;validator_version:str;validated_at:datetime

@dataclass(frozen=True)
class CommissionerReviewPlanInstruction:
 review_id:str;player_id:str;league_team_id:str;agreement_id:str|None;outcome:str;planned_contract_action:str;planned_publication_action:str;planned_dead_cap_action:str;planned_roster_action:str;blockers:tuple[str,...];warnings:tuple[str,...];evidence_fingerprint:str;review_fingerprint:str

def _fingerprint(value:Any)->str:
 def clean(v):
  if hasattr(v,"__dataclass_fields__"):return clean(asdict(v))
  if isinstance(v,Mapping):return {str(k):clean(x) for k,x in sorted(v.items(),key=lambda x:str(x[0]))}
  if isinstance(v,(tuple,list,set,frozenset)):return [clean(x) for x in v]
  if isinstance(v,datetime):return v.astimezone(timezone.utc).isoformat()
  return v
 return stable_fingerprint(clean(value))

class CommissionerPopulationBuilder:
 def build(self,client,league_id:str,source_season:int,target_season:int,owner_escalations:Sequence[Mapping[str,Any]]=(),conflicts:Sequence[Mapping[str,Any]]=())->CommissionerPopulation:
  report=RolloverAuthorityService(client).build_rollover_readiness_report(league_id);cases=[];seen=set();blockers=[]
  for item in report.roster_exceptions:
   if item.classification not in {"ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED","EXPIRED_UNROSTERED_PUBLICATION_PENDING"}:continue
   review_type="active_off_roster_liability" if item.classification.startswith("ACTIVE") else "expired_unrostered_publication_candidate"
   active=review_type=="active_off_roster_liability"
   evidence={"agreement_status":item.contract_status,"roster_status":item.roster_status,"source_salary":item.evidence.get("salary"),"source_contract_years":item.evidence.get("years_remaining"),"original_team_id":item.team_id,"publication_blocked":True,"acquisition_blocked":True,"second_agreement_blocked":active,"termination_inferred":False,"dead_cap_inferred":False}
   cases.append(self._case(league_id,source_season,target_season,item.player_id,item.player_name,item.team_id,item.agreement_id,None,review_type,item.contract_status,item.roster_status,evidence))
  for row in (*owner_escalations,*conflicts):
   cases.append(self._case(league_id,source_season,target_season,str(row.get("player_id") or ""),str(row.get("player_name") or ""),str(row.get("league_team_id") or ""),str(row.get("agreement_id") or ""),row.get("contract_event_id"),str(row.get("review_type") or "owner_escalation"),str(row.get("agreement_status") or "unknown"),str(row.get("roster_status") or "unknown"),dict(row.get("evidence") or {})))
  cases.sort(key=lambda x:(x.review_type,x.player_id,x.agreement_id,x.league_team_id))
  for case in cases:
   key=(case.review_type,case.player_id,case.agreement_id)
   if key in seen:blockers.append(f"duplicate_commissioner_case:{case.review_type}:{case.player_id}:{case.agreement_id}")
   seen.add(key)
   if case.blockers:blockers.extend(case.blockers)
  actual=len(cases);expected=EXPECTED_COMMISSIONER_CASES+len(owner_escalations)+len(conflicts)
  if actual!=expected:blockers.append(f"commissioner_population_count:{actual}:expected:{expected}")
  basis=[{"type":x.review_type,"player":x.player_id,"agreement":x.agreement_id,"team":x.league_team_id,"evidence":x.evidence_fingerprint} for x in cases]
  return CommissionerPopulation(tuple(cases),actual,expected,actual-expected,tuple(dict.fromkeys(blockers)),(),_fingerprint({"league":league_id,"source":source_season,"target":target_season,"cases":basis}))
 def _case(self,league,source,target,player,name,team,agreement,event,review_type,agreement_status,roster_status,evidence):
  local=[]
  if not player:local.append("identity_unresolved")
  if not team:local.append("team_unresolved")
  if not agreement:local.append("agreement_unresolved")
  active=review_type=="active_off_roster_liability";salary=evidence.get("source_salary")
  packet={"league":league,"source":source,"target":target,"player":player,"team":team,"agreement":agreement,"event":event,"type":review_type,"agreement_status":agreement_status,"roster_status":roster_status,"evidence":evidence,"blockers":local}
  return CommissionerReviewCandidate(league,source,target,player,name,team,agreement,event,review_type,agreement_status,roster_status,None if salary is None else str(salary),int(evidence.get("source_contract_years") or 0),"active_liability" if active else "expired","blocked" if active else "candidate_only","blocked" if active else "deferred","blocked" if active else "none","none_without_qualifying_event",MappingProxyType(evidence),tuple(local),(),_fingerprint(packet),("contract_agreements","contract_events","contract_seasons","season_roster_assignments"))

class CommissionerReviewValidator:
 def validate(self,review:Mapping[str,Any],outcome:str,*,evidence:Mapping[str,Any]|None=None,termination_event_id:str|None=None,dead_cap_event_id:str|None=None,publication_reference:str|None=None,retained_agreement_id:str|None=None,validated_at:datetime|None=None)->CommissionerReviewValidationResult:
  evidence=dict(evidence or {});blockers=[];warnings=[];kind=str(review.get("review_type") or "");state=str(review.get("review_state") or "pending");allowed=outcome in OUTCOME_MATRIX.get(kind,frozenset())
  if not allowed:blockers.append("outcome_not_allowed_for_review_type")
  if state not in {"under_review","decision_ready","evidence_required","superseded"}:blockers.append("review_state_not_decidable")
  identity={"player":bool(review.get("player_id")),"team":bool(review.get("league_team_id"))};contract={"agreement":bool(review.get("agreement_id")),"active_agreement":str(review.get("agreement_status"))=="active"};roster={"known":bool(review.get("roster_status"))}
  termination={"event":bool(termination_event_id),"authority":bool(evidence.get("termination_authority")),"reason":bool(evidence.get("termination_reason")),"season":bool(evidence.get("effective_season")),"validated_state":bool(evidence.get("validated_agreement_state"))}
  if outcome=="approve_termination" and not all(termination.values()):blockers.append("termination_evidence_incomplete")
  amount=float(evidence.get("dead_cap_amount") or 0);dead_cap={"qualifying_event":bool(dead_cap_event_id or termination_event_id),"calculation_fingerprint":bool(evidence.get("dead_cap_calculation_fingerprint")) if amount else True}
  if outcome=="approve_dead_cap" and (not all(dead_cap.values()) or amount<=0):blockers.append("dead_cap_evidence_incomplete")
  publication={"no_active_agreement":not contract["active_agreement"] or bool(termination_event_id),"eligible":bool(evidence.get("publication_eligible")),"authority_deferred":not bool(evidence.get("publication_authority_initialized"))}
  if outcome=="approve_publication" and not all(publication.values()):blockers.append("publication_evidence_incomplete")
  duplicate={"no_duplicate_active_agreement":not bool(evidence.get("duplicate_active_agreement"))}
  if outcome in {"retain_contract","preserve_active_liability"} and (not contract["agreement"] or not duplicate["no_duplicate_active_agreement"]):blockers.append("retention_conflict")
  if kind=="active_off_roster_liability" and outcome=="approve_publication" :blockers.append("active_liability_publication_forbidden")
  if kind=="active_off_roster_liability" and outcome=="preserve_active_liability" and str(review.get("source_salary")) not in {"3","3.0","3.00"}:warnings.append("active_liability_salary_not_expected_three")
  checks={"legal_outcome":allowed,"decidable_state":"review_state_not_decidable" not in blockers};now=validated_at or datetime.now(timezone.utc);packet={"review":review.get("id"),"type":kind,"state":state,"outcome":outcome,"evidence":evidence,"termination":termination_event_id,"dead_cap":dead_cap_event_id,"publication":publication_reference,"retained":retained_agreement_id,"blockers":blockers}
  evidence_fp=_fingerprint(packet);authority="deferred_to_authority_preparation";valid=not blockers
  return CommissionerReviewValidationResult(valid,valid and outcome not in {"blocked","require_identity_resolution","require_contract_resolution","require_waiver_resolution","require_rookie_draft_resolution"},str(review.get("id") or ""),kind,outcome,MappingProxyType(checks),MappingProxyType(identity),MappingProxyType(contract),MappingProxyType(roster),MappingProxyType(termination),MappingProxyType(publication),MappingProxyType(dead_cap),MappingProxyType({"blocked":kind=="active_off_roster_liability"}),MappingProxyType(duplicate),MappingProxyType({"publication":authority,"dead_cap":authority,"cap":authority}),tuple(dict.fromkeys(blockers)),tuple(warnings),evidence_fp,_fingerprint({"evidence":evidence_fp,"validator":VALIDATOR_VERSION}),VALIDATOR_VERSION,now)

def commissioner_review_readiness(execution:Mapping[str,Any]|None,reviews:Sequence[Mapping[str,Any]],owner_decisions:Sequence[Mapping[str,Any]]=()):
 if not execution:return {"status":"execution_control_ready","blockers":("rollover execution not created",)}
 if execution.get("status")!="decision_window_closed":return {"status":"commissioner_review_required","blockers":("owner decision window not closed",)}
 unresolved_owner=[str(x.get("id")) for x in owner_decisions if x.get("decision_status") in {"waiting_for_owner","recontract_submitted","recontract_invalid","blocked"}]
 if unresolved_owner:return {"status":"decision_resolution_required","blockers":tuple(unresolved_owner)}
 if not reviews:return {"status":"commissioner_review_required","blockers":("commissioner reviews not initialized",)}
 blocked=[str(x.get("id")) for x in reviews if x.get("review_state") in {"blocked","evidence_required"}]
 if blocked:return {"status":"commissioner_review_blocked","blockers":tuple(blocked)}
 unfinished=[str(x.get("id")) for x in reviews if x.get("review_state") not in {"approved","rejected"}]
 if unfinished:return {"status":"commissioner_review_in_progress","blockers":tuple(unfinished)}
 return {"status":"authority_preparation_required","review_status":"commissioner_review_complete","blockers":()}

def to_plan_instruction(review:Mapping[str,Any])->CommissionerReviewPlanInstruction:
 outcome=str(review.get("outcome") or "blocked");contract="preserve" if outcome in {"preserve_active_liability","retain_contract","reject_termination"} else "terminate" if outcome=="approve_termination" else "none";publication="candidate" if outcome=="approve_publication" else "blocked" if outcome in {"reject_publication","preserve_active_liability"} else "none";dead_cap="calculate_from_qualifying_event" if outcome in {"approve_termination","approve_dead_cap"} else "none";roster="future_plan_only"
 return CommissionerReviewPlanInstruction(str(review.get("id")),str(review.get("player_id")),str(review.get("league_team_id")),review.get("agreement_id"),outcome,contract,publication,dead_cap,roster,tuple(review.get("blockers") or ()),tuple(review.get("warnings") or ()),str(review.get("evidence_fingerprint") or ""),str(review.get("review_fingerprint") or ""))

class CommissionerReviewService:
 """Authenticated commissioner-session facade; no service-role impersonation path."""
 def __init__(self,client):self.client=client
 def initialize(self,payload):return self.client.rpc("initialize_rollover_commissioner_reviews_authenticated",{"p_request":payload}).execute().data
 def begin(self,payload):return self.client.rpc("begin_rollover_commissioner_review_authenticated",{"p_request":payload}).execute().data
 def submit(self,payload):return self.client.rpc("submit_rollover_commissioner_review_authenticated",{"p_request":payload}).execute().data
 def supersede(self,payload):return self.client.rpc("supersede_rollover_commissioner_review_authenticated",{"p_request":payload}).execute().data
 def cancel(self,payload):return self.client.rpc("cancel_rollover_commissioner_review_authenticated",{"p_request":payload}).execute().data
