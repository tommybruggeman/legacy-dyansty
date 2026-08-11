from __future__ import annotations

from dataclasses import asdict,dataclass
from datetime import datetime,timezone
from typing import Any,Iterable

from season_engine.rollover_service import stable_fingerprint


REQUIRED_INPUTS=("owner_option_deadline","failure_to_act_outcome")
SEVEN_DAY_NOTICE_RULE="SEVEN_CALENDAR_DAYS_AFTER_OFFICIAL_COMMISSIONER_ROLLOVER_NOTICE"
RELEASE_TO_HOLD="RELEASE_AT_ROLLOVER_TO_COMMISSIONER_HOLD"


@dataclass(frozen=True)
class PreparedPolicyDraft:
    payload:dict[str,Any];fingerprint:str;complete:bool;missing_inputs:tuple[str,...]
    validation_errors:tuple[str,...];writes_performed:int=0


@dataclass(frozen=True)
class PolicyScenario:
    scenario_id:str;name:str;policy_resolved:int;owner_decisions:int;commissioner_decisions:int
    publication_candidates:int;roster_actions:int;contract_actions:int;cap_impact:str
    roster_legality_risk:str;operational_complexity:str;rollback_difficulty:str
    implementation_requirements:tuple[str,...];blockers:tuple[str,...]
    advantages:tuple[str,...];disadvantages:tuple[str,...]


@dataclass(frozen=True)
class CommissionerPolicyApprovalPacket:
    league_id:str;source_season:int;target_season:int;draft_identifier:str;draft_version:int
    policy_fingerprint:str;current_authority_summary:dict[str,Any];proposed_choices:dict[str,Any]
    unresolved_required_choices:tuple[str,...];player_impact_summary:dict[str,Any]
    cap_impact_summary:dict[str,Any];publication_impact_summary:dict[str,Any]
    taxi_ir_impact_summary:dict[str,Any];active_off_roster_impact_summary:dict[str,Any]
    write_path_dependencies:tuple[str,...];rollover_dependencies:tuple[str,...]
    blockers:tuple[str,...];warnings:tuple[str,...];irreversible_consequences:tuple[str,...]
    commissioner_acknowledgments:tuple[str,...];approval_status:str;generated_at:str
    provenance:tuple[str,...];execution_command:None=None


class CommissionerPolicyDraftService:
    def prepare(self,league_id:str,*,deadline:str|None=None,failure_to_act_outcome:str|None=None)->PreparedPolicyDraft:
        missing=[]
        if not deadline:missing.append("owner_option_deadline")
        if not failure_to_act_outcome:missing.append("failure_to_act_outcome")
        errors=[]
        if deadline and deadline != SEVEN_DAY_NOTICE_RULE:errors.append("unsupported owner-option deadline rule")
        if failure_to_act_outcome and failure_to_act_outcome != RELEASE_TO_HOLD:errors.append("unsupported failure-to-act outcome")
        metadata={
            "active_roster_treatment":"owner_option_window",
            "taxi_policy":"automatic_commissioner_review; taxi unlock does not alter contract; no automatic taxi return",
            "ir_policy":"general owner option window plus roster eligibility reconciliation; IR never alters contract lifecycle",
            "overlapping_active_agreement_policy":"blocked",
            "acquisition_eligibility_policy":"separate publication and acquisition approval required",
            "commissioner_hold_behavior":"hold every candidate until rollover disposition and publication initialization",
            "target_cap_validation_policy":"normalized salaries + approved adjustments + initialized dead-cap authority + all teams",
            "effective_boundary":"future confirmed 2025-to-2026 rollover execution step",
            "approval_requirements":["commissioner approval","fingerprint match","all mandatory inputs selected"],
            "execution_prerequisites":["publication authority initialized","dead-cap authority initialized","cap authority validated","write paths ready"],
            "failure_to_act_outcome":failure_to_act_outcome,
            "deadline_resolution":"each rollover notice timestamp plus seven calendar days produces the exact deadline timestamp",
            "missed_deadline_execution":"release is planned only inside the separately approved rollover execution",
            "post_release_state":"commissioner_hold",
            "publication_requirements":["publication authority initialized","contract-conflict validation","waiver validation","rookie/draft validation","commissioner authorization"],
        }
        payload={"league_id":league_id,"source_season":2025,"target_season":2026,"version":1,"status":"draft",
            "rostered_expired_policy":"owner_option_window","off_roster_active_policy":"retain_liability_block_second_agreement",
            "free_agent_publication_policy":"commissioner_hold_until_rollover_resolution","waiver_policy":"waiver_status_must_resolve_before_acquisition",
            "extension_deadline":deadline,"taxi_policy":metadata["taxi_policy"],"ir_policy":metadata["ir_policy"],
            "dead_cap_policy":"zero_percent_only_after_qualifying_early_termination_and_initialized_authority",
            "early_termination_policy":"explicit_approved_audited_event_required","cap_adjustment_policy":"season_scoped_positive_consumes_negative_credits",
            "draft_rookie_policy":"draft_and_rookie_locks_resolve_before_publication","effective_at":None,"approved_by":None,"approved_at":None,
            "metadata":metadata}
        fp=stable_fingerprint(payload);payload={**payload,"fingerprint":fp}
        return PreparedPolicyDraft(payload,fp,not missing and not errors,tuple(missing),tuple(errors),0)

    def scenarios(self)->tuple[PolicyScenario,...]:
        common=("publication remains a later explicit action","natural expiration creates no dead cap")
        return (
            PolicyScenario("A","Owner option window",108,108,13,11,0,0,"zero contract salary for expired players","roster-limit risk remains until later owner action and rollover execution","medium","low",("resolve each notice to an exact deadline","normalized extension/re-contract path","separately approved rollover executor"),("commissioner approval required",),("owner choice","avoids premature release","missed deadlines remain on commissioner hold after later rollover release"),("requires owner coordination","publication remains separate")),
            PolicyScenario("B","Automatic release",108,0,13,119,108,0,"expired players already have zero active salary","Taxi/IR exceptions must be separated","high","high",("Taxi rule","IR rule","roster removal executor","publication holds"),("commissioner selection required","Taxi/IR treatment required"),("simple league-wide disposition",),("large irreversible roster plan","119 candidates require later publication review")),
            PolicyScenario("C","Per-player review",0,0,121,11,0,0,"no automatic cap change","none until decisions execute","high","low",("121 commissioner decisions",),(),("maximum control",),("maximum workload","no decision reduction")),
            PolicyScenario("D","Retain uncontracted",108,0,13,11,0,0,"expired players carry zero normalized salary","roster limits and trade/acquisition restrictions unresolved","medium","low",("deadline or commissioner release condition","roster legality validation"),("retention boundary required",),("no premature roster removal",),("may create roster congestion",)),
        )

    def reduce(self,draft:PreparedPolicyDraft,exceptions:Iterable[Any]):
        rostered=[];off_roster=[];publication=[]
        for item in exceptions:
            if item.classification=="ROSTERED_EXPIRED_POLICY_UNDEFINED":rostered.append(item.player_id)
            elif item.classification=="ACTIVE_OFF_ROSTER_POLICY_REVIEW_REQUIRED":off_roster.append(item.player_id)
            elif item.classification=="EXPIRED_UNROSTERED_PUBLICATION_PENDING":publication.append(item.player_id)
        return {"starting_decisions":len(rostered)+len(off_roster)+len(publication),
            "policy_resolved":len(rostered) if draft.complete else 0,"blocked_by_missing_input":len(rostered) if draft.missing_inputs else 0,
            "owner_actions_required":len(rostered),"commissioner_actions_required":len(off_roster)+len(publication),
            "publication_initialization_required":tuple(publication),"write_path_cutover_required":tuple(rostered+off_roster+publication),
            "rostered_expired_players":tuple(rostered),"individual_review_players":tuple(off_roster+publication),"writes_performed":0}

    def approval_packet(self,draft:PreparedPolicyDraft,impact:dict[str,Any])->CommissionerPolicyApprovalPacket:
        return CommissionerPolicyApprovalPacket(draft.payload["league_id"],2025,2026,"rollover-policy:2025:2026:v1",1,draft.fingerprint,
            {"league":2025,"contracts":2026,"cap":2025,"publication":"schema_only","dead_cap":"uninitialized"},draft.payload,draft.missing_inputs,
            impact,{"expired_active_salary":0,"cap_authority":"not initialized"},{"planned_candidates":11,"published":0,"acquisition_eligible":0,"hold_required":True},
            {"taxi":"commissioner review","ir":"owner window plus eligibility reconciliation"},{"players":["Jalen Milroe","Tre Harris"],"liability_remains":True,"second_agreement_blocked":True},
            ("extension/re-contract","release","publication","dead-cap","cap validation","roster executor"),("policy approval","authority initialization","write cutover","confirmed rollover"),
            tuple(draft.missing_inputs)+tuple(draft.validation_errors),("Approval does not execute rollover.",),("Roster removals and publication become irreversible after later execution.",),
            ("seven-calendar-day notice rule selected","release-at-rollover-to-commissioner-hold selected","Taxi/IR handling accepted","publication remains separately authorized","no action executes on approval"),"not_approvable" if not draft.complete else "approvable_unapproved",
            datetime.now(timezone.utc).isoformat(),("league_rules","normalized contracts","season_roster_assignments","Phase 3B.4D exceptions"),None)

    def readiness(self,draft:PreparedPolicyDraft|None):
        if draft is None:return {"status":"policy_draft_required","blockers":("policy draft missing",)}
        if not draft.complete:return {"status":"policy_input_required","blockers":draft.missing_inputs+draft.validation_errors}
        return {"status":"commissioner_approval_required","blockers":("commissioner approval required",)}
