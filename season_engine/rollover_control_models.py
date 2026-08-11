from __future__ import annotations
from dataclasses import dataclass,field,fields,is_dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any,Mapping

from season_engine.rollover_service import stable_fingerprint

class StrEnum(str,Enum):pass
class RolloverExecutionStatus(StrEnum):
    DRAFT="draft";PREFLIGHT_READY="preflight_ready";NOTICE_OPEN="notice_open";DECISION_WINDOW_OPEN="decision_window_open";DECISION_WINDOW_CLOSED="decision_window_closed";AUTHORITY_INITIALIZING="authority_initializing";AUTHORITY_READY="authority_ready";PLAN_READY="plan_ready";AWAITING_EXECUTION_APPROVAL="awaiting_execution_approval";EXECUTION_READY="execution_ready";EXECUTING="executing";COMMITTED="committed";VALIDATING="validating";COMPLETED="completed";FAILED_PRECOMMIT="failed_precommit";FAILED_POSTCOMMIT_VALIDATION="failed_postcommit_validation";CANCELLED="cancelled"
class RolloverApprovalStatus(StrEnum):NOT_REQUIRED="not_required";PENDING="pending";APPROVED="approved";REJECTED="rejected";SUPERSEDED="superseded"
class RolloverOwnerDecisionStatus(StrEnum):
    WAITING_FOR_OWNER="waiting_for_owner";RECONTRACT_SUBMITTED="recontract_submitted";RECONTRACT_INVALID="recontract_invalid";RECONTRACT_VALIDATED="recontract_validated";DECLINE_SUBMITTED="decline_submitted";COMMISSIONER_REVIEW_REQUESTED="commissioner_review_requested";NO_RESPONSE="no_response";PLANNED_RETENTION="planned_retention";PLANNED_RELEASE="planned_release";BLOCKED="blocked";EXECUTION_READY="execution_ready";EXECUTED_RETAINED="executed_retained";EXECUTED_RELEASED="executed_released";COMMISSIONER_HOLD="commissioner_hold";CANCELLED="cancelled"
class RolloverOwnerChoice(StrEnum):RECONTRACT="recontract";DECLINE="decline";COMMISSIONER_REVIEW="commissioner_review"
class RolloverDecisionExecutionStatus(StrEnum):PENDING="pending";BLOCKED="blocked";READY="ready";EXECUTING="executing";EXECUTED="executed";CANCELLED="cancelled"
class RolloverCommissionerReviewType(StrEnum):ACTIVE_OFF_ROSTER_LIABILITY="active_off_roster_liability";EXPIRED_UNROSTERED_PUBLICATION_CANDIDATE="expired_unrostered_publication_candidate";OWNER_ESCALATION="owner_escalation";IDENTITY_CONFLICT="identity_conflict";WAIVER_CONFLICT="waiver_conflict";ROOKIE_DRAFT_CONFLICT="rookie_draft_conflict";CONTRACT_CONFLICT="contract_conflict";COMMISSIONER_OVERRIDE="commissioner_override"
class RolloverCommissionerReviewStatus(StrEnum):REVIEW_REQUIRED="review_required";EVIDENCE_INCOMPLETE="evidence_incomplete";DECISION_PENDING="decision_pending";RETAIN_LIABILITY="retain_liability";APPROVE_RELEASE="approve_release";APPROVE_PUBLICATION_HOLD="approve_publication_hold";BLOCK_PUBLICATION="block_publication";APPROVE_TERMINATION="approve_termination";ACTION_VALIDATED="action_validated";EXECUTION_READY="execution_ready";EXECUTED="executed";CANCELLED="cancelled"
class RolloverExecutionPlanStatus(StrEnum):DRAFT="draft";INVALID="invalid";READY="ready";APPROVED="approved";SUPERSEDED="superseded";EXECUTED="executed"
class RolloverExecutionLockScope(StrEnum):CONTRACTS="contracts";ROSTERS="rosters";ROSTER_SYNC="roster_sync";SLEEPER_SYNC="sleeper_sync";TRANSACTIONS="transactions";TRADES="trades";FREE_AGENTS="free_agents";WAIVERS="waivers";TAXI="taxi";IR="ir";CAP_ADJUSTMENTS="cap_adjustments";LEAGUE_RULES="league_rules";SEASON_AUTHORITY="season_authority";COMMISSIONER_OVERRIDES="commissioner_overrides";ROLLOVER_GLOBAL="rollover_global"
class RolloverExecutionLockStatus(StrEnum):PENDING="pending";ACTIVE="active";RELEASED="released";EXPIRED="expired";CANCELLED="cancelled"
class RolloverValidationRunType(StrEnum):DRY_RUN="dry_run";PRECOMMIT="precommit";POSTCOMMIT="postcommit";VISIBLE_CUTOVER="visible_cutover"
class RolloverValidationStatus(StrEnum):PENDING="pending";PASSED="passed";FAILED="failed";WARNING="warning";BLOCKED="blocked"

EXECUTION_TRANSITIONS={
 "draft":{"preflight_ready","cancelled","failed_precommit"},"preflight_ready":{"notice_open","cancelled","failed_precommit"},"notice_open":{"decision_window_open","cancelled","failed_precommit"},"decision_window_open":{"decision_window_closed","cancelled","failed_precommit"},"decision_window_closed":{"authority_initializing","cancelled","failed_precommit"},"authority_initializing":{"authority_ready","failed_precommit","cancelled"},"authority_ready":{"plan_ready","failed_precommit","cancelled"},"plan_ready":{"awaiting_execution_approval","failed_precommit","cancelled"},"awaiting_execution_approval":{"execution_ready","plan_ready","cancelled","failed_precommit"},"execution_ready":{"executing","plan_ready","failed_precommit"},"executing":{"committed","failed_precommit"},"committed":{"validating"},"validating":{"completed","failed_postcommit_validation"},"failed_precommit":{"preflight_ready","notice_open","decision_window_open","decision_window_closed","authority_initializing","plan_ready","cancelled"},"failed_postcommit_validation":{"validating"}}
OWNER_TRANSITIONS={"waiting_for_owner":{"recontract_submitted","decline_submitted","commissioner_review_requested","no_response","cancelled"},"recontract_submitted":{"recontract_invalid","recontract_validated","cancelled"},"recontract_invalid":{"waiting_for_owner","blocked","cancelled"},"recontract_validated":{"planned_retention","blocked","cancelled"},"decline_submitted":{"planned_release","cancelled"},"no_response":{"planned_release"},"planned_retention":{"execution_ready","blocked","cancelled"},"planned_release":{"execution_ready","blocked","cancelled"},"execution_ready":{"executed_retained","executed_released","blocked"},"executed_released":{"commissioner_hold"},"blocked":{"execution_ready","cancelled"}}
REVIEW_TRANSITIONS={"review_required":{"evidence_incomplete","decision_pending","cancelled"},"evidence_incomplete":{"decision_pending","cancelled"},"decision_pending":{"retain_liability","approve_release","approve_publication_hold","block_publication","approve_termination","cancelled"},"retain_liability":{"action_validated","cancelled"},"approve_release":{"action_validated","cancelled"},"approve_publication_hold":{"action_validated","cancelled"},"block_publication":{"action_validated","cancelled"},"approve_termination":{"action_validated","cancelled"},"action_validated":{"execution_ready","decision_pending","cancelled"},"execution_ready":{"executed"}}

def _enum_value(value:StrEnum|str)->str:return value.value if isinstance(value,Enum) else str(value)
def legal_transition(mapping:Mapping[str,set[str]],old:StrEnum|str,new:StrEnum|str)->bool:
    old_value,new_value=_enum_value(old),_enum_value(new);return new_value in mapping.get(old_value,set()) or new_value==old_value
def _aware(value:datetime|None)->bool:return value is None or value.tzinfo is not None and value.utcoffset() is not None
def deterministic_payload(value:Any)->dict[str,Any]:
    if not is_dataclass(value):raise TypeError("deterministic payload requires a dataclass")
    raw={item.name:getattr(value,item.name) for item in fields(value)}
    return {k:(v.value if isinstance(v,Enum) else v.isoformat() if isinstance(v,datetime) else dict(v) if isinstance(v,Mapping) else v) for k,v in raw.items()}
def model_fingerprint(value:Any)->str:return stable_fingerprint(deterministic_payload(value))

@dataclass(frozen=True)
class RolloverExecution:
    id:str;league_id:str;source_season:int;target_season:int;policy_id:str;policy_fingerprint:str;version:int;status:RolloverExecutionStatus;approval_status:RolloverApprovalStatus;notice_timestamp:datetime|None=None;owner_deadline:datetime|None=None;metadata:Mapping[str,Any]=field(default_factory=lambda:MappingProxyType({}))
    def __post_init__(self):
        if self.target_season!=self.source_season+1:raise ValueError("invalid season boundary")
        if not _aware(self.notice_timestamp) or not _aware(self.owner_deadline):raise ValueError("timestamps must be timezone-aware")
        if self.notice_timestamp and self.owner_deadline and self.owner_deadline<=self.notice_timestamp:raise ValueError("deadline must follow notice")
@dataclass(frozen=True)
class RolloverOwnerDecision:
    id:str;rollover_execution_id:str;league_id:str;source_season:int;target_season:int;league_team_id:str;player_id:str;agreement_id:str;decision_status:RolloverOwnerDecisionStatus;execution_status:RolloverDecisionExecutionStatus;owner_choice:RolloverOwnerChoice|None=None;evidence:Mapping[str,Any]=field(default_factory=lambda:MappingProxyType({}))
@dataclass(frozen=True)
class RolloverOwnerDecisionRevision:id:str;owner_decision_id:str;rollover_execution_id:str;revision_number:int;new_status:RolloverOwnerDecisionStatus;idempotency_key:str
@dataclass(frozen=True)
class RolloverCommissionerReview:id:str;rollover_execution_id:str;league_id:str;source_season:int;target_season:int;player_id:str;review_type:RolloverCommissionerReviewType;review_status:RolloverCommissionerReviewStatus;execution_status:RolloverDecisionExecutionStatus;evidence_complete:bool=False;action_validated:bool=False
@dataclass(frozen=True)
class RolloverCommissionerReviewEvent:id:str;commissioner_review_id:str;rollover_execution_id:str;event_type:str;idempotency_key:str
@dataclass(frozen=True)
class RolloverExecutionPlan:id:str;rollover_execution_id:str;league_id:str;source_season:int;target_season:int;plan_version:int;status:RolloverExecutionPlanStatus;execution_plan_fingerprint:str;plan_payload:Mapping[str,Any]
@dataclass(frozen=True)
class RolloverExecutionLock:id:str;rollover_execution_id:str;league_id:str;lock_scope:RolloverExecutionLockScope;status:RolloverExecutionLockStatus;lock_token:str
@dataclass(frozen=True)
class RolloverValidationResult:id:str;rollover_execution_id:str;validation_run_type:RolloverValidationRunType;validation_status:RolloverValidationStatus;invariant_name:str;invariant_domain:str;severity:str;passed:bool;validator_version:str
