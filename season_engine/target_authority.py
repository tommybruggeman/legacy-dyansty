from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from season_engine.rollover_service import stable_fingerprint


class PolicyValidationError(ValueError): pass
class AuthorityValidationError(ValueError): pass


class RosteredExpiredPolicy(str, Enum):
    AUTOMATIC_RELEASE_AT_ROLLOVER="automatic_release_at_rollover"
    RETAIN_UNCONTRACTED_UNTIL_DEADLINE="retain_uncontracted_until_deadline"
    COMMISSIONER_REVIEW_PER_PLAYER="commissioner_review_per_player"
    POSITION_OR_ROSTER_STATUS_POLICY="position_or_roster_status_policy"
    OWNER_OPTION_WINDOW="owner_option_window"


class OffRosterActivePolicy(str, Enum):
    RETAIN_LIABILITY_BLOCK_SECOND_AGREEMENT="retain_liability_block_second_agreement"
    RETAIN_LIABILITY_COMMISSIONER_APPROVAL="retain_liability_commissioner_approval"
    EARLY_TERMINATION_ONLY="early_termination_only"


@dataclass(frozen=True)
class LeagueRolloverPolicy:
    league_id:str;source_season:int;target_season:int;version:int;status:str
    rostered_expired_policy:str|None=None;off_roster_active_policy:str|None=None
    free_agent_publication_policy:str|None=None;waiver_policy:str|None=None
    extension_deadline:str|None=None;taxi_policy:str|None=None;ir_policy:str|None=None
    dead_cap_policy:str|None=None;early_termination_policy:str|None=None
    cap_adjustment_policy:str|None=None;draft_rookie_policy:str|None=None
    approved_by:str|None=None;approved_at:str|None=None;metadata:dict[str,Any]|None=None
    fingerprint:str=""

    def validated(self)->"LeagueRolloverPolicy":
        errors=[]
        if not self.league_id or self.target_season!=self.source_season+1:errors.append("league_and_sequential_seasons_required")
        if self.status not in {"draft","pending_approval","approved","active","superseded"}:errors.append("invalid_policy_status")
        if self.status in {"approved","active"} and (not self.approved_by or not self.approved_at):errors.append("commissioner_approval_required")
        if self.rostered_expired_policy in {RosteredExpiredPolicy.OWNER_OPTION_WINDOW.value,RosteredExpiredPolicy.RETAIN_UNCONTRACTED_UNTIL_DEADLINE.value} and not self.extension_deadline:errors.append("owner_or_retention_window_requires_deadline")
        if self.rostered_expired_policy==RosteredExpiredPolicy.AUTOMATIC_RELEASE_AT_ROLLOVER.value and (not self.taxi_policy or not self.ir_policy):errors.append("automatic_release_requires_taxi_and_ir_policy")
        if self.rostered_expired_policy==RosteredExpiredPolicy.AUTOMATIC_RELEASE_AT_ROLLOVER.value and not self.free_agent_publication_policy:errors.append("release_requires_publication_or_hold_outcome")
        if self.dead_cap_policy=="natural_expiration":errors.append("natural_expiration_never_creates_dead_cap")
        if self.off_roster_active_policy=="cancel_on_roster_absence":errors.append("roster_absence_does_not_terminate_contract")
        if self.free_agent_publication_policy=="automatic" and self.rostered_expired_policy in {None,RosteredExpiredPolicy.RETAIN_UNCONTRACTED_UNTIL_DEADLINE.value}:errors.append("cannot_publish_players_who_remain_rostered")
        if errors:raise PolicyValidationError(",".join(errors))
        basis=asdict(replace(self,fingerprint=""));return replace(self,fingerprint=stable_fingerprint(basis))

    def supersede(self,**changes)->"LeagueRolloverPolicy":
        if self.status not in {"active","approved"}:raise PolicyValidationError("only_approved_or_active_policy_requires_superseding_version")
        return replace(self,version=self.version+1,status="draft",approved_by=None,approved_at=None,fingerprint="",**changes).validated()


@dataclass(frozen=True)
class PolicyOptionEffect:
    option:str;automatically_resolved:int;individual_review:int;proposed_action:str;blockers:tuple[str,...]


@dataclass(frozen=True)
class PublicationCandidate:
    league_id:str;player_id:str;prior_team_id:str;agreement_id:str;agreement_state:str;roster_state:str
    season:int;publication_reason:str;publication_status:str;acquisition_status:str;waiver_status:str
    rookie_draft_status:str;commissioner_hold:bool;blockers:tuple[str,...];warnings:tuple[str,...]
    provenance:tuple[str,...];idempotency_key:str


@dataclass(frozen=True)
class DeadCapSeasonAuthority:
    league_id:str;season:int;status:str;team_count:int;complete_team_ids:tuple[str,...]
    obligation_count:int;total:Decimal;source_fingerprint:str;trusted_zero:bool
    blockers:tuple[str,...]=()


@dataclass(frozen=True)
class TargetCapAuthority:
    league_id:str;season:int;status:str;salary_cap_limit:Decimal;contract_source:str
    adjustment_source:str;dead_cap_source:str;all_teams_represented:bool
    source_fingerprint:str;readiness_fingerprint:str;blockers:tuple[str,...]=()


class CommissionerPolicyService:
    def draft(self,**values)->LeagueRolloverPolicy:return LeagueRolloverPolicy(**values).validated()
    def options(self,count=108)->tuple[PolicyOptionEffect,...]:
        return (
            PolicyOptionEffect(RosteredExpiredPolicy.AUTOMATIC_RELEASE_AT_ROLLOVER.value,count,0,"plan release, then publication/hold",("commissioner_selection_required","taxi_ir_treatment_required")),
            PolicyOptionEffect(RosteredExpiredPolicy.RETAIN_UNCONTRACTED_UNTIL_DEADLINE.value,count,0,"retain at zero contract salary until deadline",("deadline_required",)),
            PolicyOptionEffect(RosteredExpiredPolicy.COMMISSIONER_REVIEW_PER_PLAYER.value,0,count,"retain individual decisions",()),
            PolicyOptionEffect(RosteredExpiredPolicy.POSITION_OR_ROSTER_STATUS_POLICY.value,0,count,"evaluate active/IR/Taxi sub-policies",("status_rules_required",)),
            PolicyOptionEffect(RosteredExpiredPolicy.OWNER_OPTION_WINDOW.value,count,0,"owner option before deadline",("deadline_required",)),
        )
    def reduce(self,policy:LeagueRolloverPolicy,exceptions:Iterable[Any]):
        resolved=[];remaining=[]
        for item in exceptions:
            if item.classification=="ROSTERED_EXPIRED_POLICY_UNDEFINED" and policy.rostered_expired_policy not in {None,RosteredExpiredPolicy.COMMISSIONER_REVIEW_PER_PLAYER.value,RosteredExpiredPolicy.POSITION_OR_ROSTER_STATUS_POLICY.value}:resolved.append(item.player_id)
            else:remaining.append(item.player_id)
        return {"resolved":tuple(resolved),"remaining":tuple(remaining),"resolved_count":len(resolved),"remaining_count":len(remaining),"writes_performed":0}


class FreeAgentPublicationService:
    MODES={"disabled","shadow","compare","normalized"}
    def __init__(self,client=None,mode="disabled",writes_enabled=False):
        if mode not in self.MODES:raise ValueError("invalid publication mode")
        self.client=client;self.mode=mode;self.writes_enabled=writes_enabled
    def list_states(self,league_id,season):
        if self.mode=="disabled" or not self.client:return []
        rows=self.client.table("free_agent_publications").select("*").eq("league_id",league_id).eq("season",season).execute().data or []
        keys=[(x.get("league_id"),x.get("player_id"),x.get("season")) for x in rows]
        if len(keys)!=len(set(keys)):raise AuthorityValidationError("duplicate_publication_state")
        return rows
    def plan_publication(self,*,league_id,player_id,team_id,agreement_id,agreement_state,roster_state,season,reason,waiver_status="unknown",rookie_draft_status="unknown",policy_approved=False):
        blockers=[]
        if agreement_state=="active":blockers.append("active_contract_conflict")
        if roster_state!="unrostered":blockers.append("player_remains_rostered")
        if not policy_approved:blockers.append("commissioner_policy_not_approved")
        key=f"free-agent-publication:{league_id}:{season}:{player_id}:v1"
        return PublicationCandidate(league_id,player_id,team_id,agreement_id,agreement_state,roster_state,season,reason,"pending","ineligible" if blockers else "commissioner_approval_required",waiver_status,rookie_draft_status,True,tuple(blockers),(),("contract_agreements","season_roster_assignments","league_rollover_policies"),key)
    def determine_acquisition_eligibility(self,state):
        if state.get("publication_status")!="published":return "ineligible"
        if state.get("commissioner_hold"):return "commissioner_approval_required"
        if state.get("waiver_status")=="locked":return "waiver_required"
        if state.get("publication_status") in {"draft_locked","rookie_locked"}:return "ineligible"
        return str(state.get("acquisition_status") or "unknown")
    def publish(self,*a,**k):
        if not self.writes_enabled:raise PermissionError("production publication writes are disabled")
        raise NotImplementedError("publication execution belongs to a later confirmed phase")
    unpublish=publish;place_commissioner_hold=publish;release_commissioner_hold=publish;set_waiver_lock=publish


class TargetAuthorityService:
    def plan_dead_cap_initialization(self,league_id,season,team_ids,obligations,qualifying_events):
        teams=tuple(sorted(set(map(str,team_ids))));seen=set();total=Decimal("0");blockers=[]
        for row in obligations:
            key=(str(row.get("team_id") or row.get("league_team_id")),str(row.get("player_id")),str(row.get("contract_agreement_id")),int(row.get("season")))
            if key in seen:blockers.append("duplicate_dead_cap_liability")
            seen.add(key);amount=Decimal(str(row.get("amount") or 0));event=str(row.get("source_event_id") or "")
            if amount and event not in qualifying_events:blockers.append("nonzero_dead_cap_requires_qualifying_early_termination")
            if str(row.get("termination_type"))=="natural_expiration" and amount:blockers.append("natural_expiration_dead_cap_forbidden")
            total+=amount
        fp=stable_fingerprint({"league":league_id,"season":season,"teams":teams,"obligations":list(obligations)})
        return DeadCapSeasonAuthority(league_id,season,"blocked" if blockers else "planned",len(teams),teams,len(seen),total,fp,not blockers and total==0,tuple(dict.fromkeys(blockers)))
    def validate_cap(self,league_id,season,team_ids,contract_salaries,adjustments,dead_cap,limit):
        teams=set(map(str,team_ids));blockers=[]
        for label,data in (("contracts",contract_salaries),("adjustments",adjustments)):
            if set(map(str,data))!=teams:blockers.append(f"{label}_team_completeness_required")
        if set(dead_cap.complete_team_ids)!=teams:blockers.append("dead_cap_team_completeness_required")
        if dead_cap.status not in {"initialized","validated"}:blockers.append("dead_cap_authority_initialization_required")
        source={"contracts":contract_salaries,"adjustments":adjustments,"dead_cap":asdict(dead_cap),"limit":str(limit)}
        fp=stable_fingerprint(source)
        return TargetCapAuthority(league_id,season,"blocked" if blockers else "validated",Decimal(str(limit)),"normalized_contract_seasons","season_scoped_cap_adjustments","dead_cap_obligations",not blockers,fp,stable_fingerprint({"source":fp,"status":"validated"}),tuple(blockers))


class TargetAuthorityRepository:
    """Typed empty-state reads. Absence never means approval, publication, or zero."""
    def __init__(self,client):self.client=client
    def _rows(self,table,league_id,season):return self.client.table(table).select("*").eq("league_id",league_id).eq("season" if table!="league_rollover_policies" else "target_season",season).execute().data or []
    def policy_state(self,league_id,season):
        rows=self._rows("league_rollover_policies",league_id,season)
        active=[x for x in rows if x.get("status") in {"approved","active"}]
        if len(active)>1:raise AuthorityValidationError("duplicate_approved_policy")
        return {"schema_available":True,"status":active[0]["status"] if active else "missing","approved":bool(active),"rows":rows,"source":"league_rollover_policies"}
    def publication_state(self,league_id,season):
        rows=self._rows("free_agent_publications",league_id,season)
        return {"schema_available":True,"status":"initialized" if rows else "no_states_initialized","published_count":sum(x.get("publication_status")=="published" for x in rows),"rows":rows,"source":"free_agent_publications"}
    def dead_cap_state(self,league_id,season):
        authority=self._rows("dead_cap_season_authorities",league_id,season)
        obligations=self._rows("dead_cap_obligations",league_id,season)
        if len(authority)>1:raise AuthorityValidationError("duplicate_dead_cap_authority")
        return {"schema_available":True,"status":authority[0]["status"] if authority else "uninitialized","authoritative_zero":bool(authority and authority[0].get("status") in {"initialized","validated"} and Decimal(str(authority[0].get("total_amount") or 0))==0),"obligations":obligations,"source":"dead_cap_season_authorities/dead_cap_obligations"}
    def cap_state(self,league_id,season):
        rows=self._rows("cap_season_authorities",league_id,season)
        if len(rows)>1:raise AuthorityValidationError("duplicate_cap_authority")
        return {"schema_available":True,"status":rows[0]["status"] if rows else "uninitialized","authoritative":bool(rows and rows[0].get("status")=="authoritative"),"rows":rows,"source":"cap_season_authorities"}


WRITE_PATH_AUTHORITY_PLAN=(
    ("trade execution","legacy contracts/cap_adjustments","normalized contracts + cap authority",True,False,"trade command key","contract event","target season","cap activation"),
    ("contract commitment","legacy contracts","contract agreements/seasons/events",True,False,"contract command key","signed","target season","write cutover"),
    ("extensions and re-signing","legacy contracts","contract agreements/seasons/events",True,False,"extension key","extended","target season","policy window"),
    ("early termination and release","legacy contracts/dead cap","contract event + dead-cap obligation",True,False,"termination key","released/dead_cap_created","target season","approved early-termination policy"),
    ("waiver claim and free-agent signing","waiver RPC","publication authority + normalized contract command",True,False,"claim/signing key","waiver/signed","target season","publication activation"),
    ("roster sync and commissioner adjustment","roster state","canonical target roster authority",False,False,"roster event key","roster event","target season","roster transition"),
    ("cap adjustment","cap_adjustments","season-scoped cap adjustment authority",False,False,"adjustment key","cap adjustment audit","target season","cap validation"),
    ("dead-cap creation","legacy processors","dead_cap_obligations",True,False,"dead-cap key","dead_cap_created","target season","qualifying termination"),
    ("season rollover","contract-only transition","rollover execution coordinator",True,False,"rollover key","rollover audit","source/target","all authorities validated"),
    ("draft transaction","draft_picks","draft authority + transaction audit",False,False,"draft transaction key","draft event","draft season","draft validation"),
)


def write_path_authority_plan():
    return tuple({"workflow":a,"current_source":b,"target_source":c,"normalized_only":d,"dual_write_required":e,"compatibility_projection_required":True,"idempotency_strategy":f,"audit_event":g,"season_authority":h,"rollout_dependency":i,"blocker":"future cutover not implemented"} for a,b,c,d,e,f,g,h,i in WRITE_PATH_AUTHORITY_PLAN)
