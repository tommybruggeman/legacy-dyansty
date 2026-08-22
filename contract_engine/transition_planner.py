from __future__ import annotations

from collections import defaultdict
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json
from typing import Any

from .transition_models import *


SUPPORTED_LIVE={"active","scheduled"}
OUT_OF_SCOPE={"voided","superseded","released"}


def stable_fingerprint(value:Any)->str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()


def build_transition_plan(*,league_id:str,source_season:int,target_season:int,league_seasons:list[dict],
    legacy_contracts:list[dict],agreements:list[dict],contract_seasons:list[dict],events:list[dict],
    teams:list[dict],roster_assignments:list[dict],cap_adjustments:list[dict],dead_cap:list[dict],
    free_agent_source:list[dict],draft_picks:list[dict],historical_facts:dict[str,list[dict]],
    expected_source_fingerprint:str|None=None,requested_at:str|None=None)->TransitionPlan:
    errors:list[dict]=[]; warnings:list[dict]=[]
    source_rows=[x for x in league_seasons if str(x.get("league_id"))==league_id and int(x.get("season"))==source_season]
    target_rows=[x for x in league_seasons if str(x.get("league_id"))==league_id and int(x.get("season"))==target_season]
    if target_season!=source_season+1:errors.append(_issue("season_sequence","Transition must advance exactly one season."))
    if len(source_rows)!=1 or len(target_rows)!=1:errors.append(_issue("season_authority","Expected one source and target league season."))
    source=source_rows[0] if len(source_rows)==1 else {}; target=target_rows[0] if len(target_rows)==1 else {}
    if not source.get("is_active") or source.get("status")!="active":errors.append(_issue("source_not_active","Source season must be active."))
    if target.get("is_active") or target.get("status")!="scheduled":errors.append(_issue("target_not_scheduled","Target must be inactive and scheduled."))
    if str(target.get("previous_league_season_id"))!=str(source.get("id")):errors.append(_issue("broken_previous_link","Target must reference source season."))
    request=TransitionRequest(league_id,source_season,target_season,str(source.get("id") or ""),str(target.get("id") or ""),
        str(source.get("status") or ""),str(target.get("status") or ""),requested_at or datetime.now(timezone.utc).isoformat(),expected_source_fingerprint=expected_source_fingerprint)

    datasets={"season_authority":league_seasons,"legacy_contracts":legacy_contracts,"agreements":agreements,
        "contract_seasons":contract_seasons,"contract_events":events,"team_ownership":teams,"current_rosters":roster_assignments,
        "cap_adjustments":cap_adjustments,"dead_cap":dead_cap,"free_agent_source":free_agent_source,"draft_picks":draft_picks,**historical_facts}
    fingerprints={k:stable_fingerprint(sorted(v,key=lambda x:json.dumps(x,sort_keys=True,default=str))) for k,v in datasets.items()}
    source_fp=stable_fingerprint(fingerprints)
    if expected_source_fingerprint and expected_source_fingerprint!=source_fp:errors.append(_issue("source_fingerprint_changed","Transition source fingerprint differs from expectation."))

    team_by_id={str(x["id"]):x for x in teams if str(x.get("league_id"))==league_id}
    legacy_by_id={str(x["id"]):x for x in legacy_contracts}
    roster_by_player={str(x["sleeper_player_id"]):x for x in roster_assignments}
    schedules:dict[str,list[dict]]=defaultdict(list)
    for row in contract_seasons:schedules[str(row.get("contract_id"))].append(row)
    player_live:dict[str,list[str]]=defaultdict(list)
    for a in agreements:
        if a.get("status") in SUPPORTED_LIVE:player_live[str(a.get("player_id"))].append(str(a.get("id")))
    for player,ids in player_live.items():
        if len(ids)>1:errors.append(_issue("overlapping_agreements",f"Player {player} has overlapping live agreements.",agreement_ids=ids))
    transition_events=[x for x in events if x.get("event_type")!="imported" and int(x.get("effective_season") or 0)>=target_season]
    if transition_events:errors.append(_issue("partial_transition_events","Existing lifecycle events suggest a prior or partial transition.",count=len(transition_events)))

    classes=[]; candidates=[]
    for a in sorted(agreements,key=lambda x:str(x.get("id"))):
        aid=str(a["id"]); rows=schedules.get(aid,[]); by_year:dict[int,list[dict]]=defaultdict(list)
        for row in rows:by_year[int(row["season"])].append(row)
        outcome=None; reasons=[]
        if str(a.get("league_id"))!=league_id or str(a.get("league_team_id")) not in team_by_id:
            outcome=INVALID_MISSING_SOURCE; reasons.append("cross-league or missing canonical team")
        elif not str(a.get("player_id") or "").strip() or not str(a.get("sleeper_player_id") or "").strip():
            outcome=INVALID_MISSING_SOURCE; reasons.append("incomplete canonical player identity")
        elif a.get("status") in OUT_OF_SCOPE:outcome=NOT_APPLICABLE
        elif a.get("status") not in SUPPORTED_LIVE:
            outcome=INVALID_MISSING_SOURCE; reasons.append(f"unsupported agreement status {a.get('status')}")
        elif any(len(v)>1 for v in by_year.values()):outcome=INVALID_DUPLICATE; reasons.append("duplicate season obligation")
        elif len(by_year.get(source_season,[]))!=1:outcome=INVALID_MISSING_SOURCE; reasons.append("missing source obligation")
        elif str(by_year[source_season][0].get("status"))=="satisfied" and by_year.get(target_season) and str(by_year[target_season][0].get("status"))=="active":
            outcome=ALREADY_TRANSITIONED; reasons.append("agreement is already transitioned")
        elif not by_year.get(target_season) and any(y>target_season for y in by_year):outcome=INVALID_GAP; reasons.append("future obligation gap")
        elif by_year.get(target_season):outcome=CONTINUES
        else:outcome=EXPIRES_AFTER_SOURCE
        for row in rows:
            if str(row.get("league_id"))!=league_id or str(row.get("league_team_id"))!=str(a.get("league_team_id")) or str(row.get("player_id"))!=str(a.get("player_id")):
                reasons.append("agreement/obligation identity mismatch")
            if Decimal(str(row.get("salary")))<0 or Decimal(str(row.get("cap_hit")))<0:reasons.append("negative financial value")
        legacy=legacy_by_id.get(str(a.get("source_legacy_contract_id")))
        legacy_outcome=CONTINUES if legacy and int(legacy.get("contract_years_left") or 0)>=2 else EXPIRES_AFTER_SOURCE if legacy else None
        if legacy_outcome!=outcome:reasons.append(f"legacy parity mismatch: {legacy_outcome} vs {outcome}")
        if reasons:
            errors.append(_issue("contract_validation",f"Agreement {aid}: {'; '.join(reasons)}",agreement_id=aid))
        source_ob=(by_year.get(source_season) or [None])[0]; target_ob=(by_year.get(target_season) or [None])[0]
        item={"agreement_id":aid,"player_id":str(a.get("player_id")),"league_team_id":str(a.get("league_team_id")),
            "outcome":outcome,"planned_source_obligation_status":"satisfied" if source_ob else None,
            "planned_target_obligation_status":"active" if target_ob else None,
            "planned_agreement_status":"expired" if outcome==EXPIRES_AFTER_SOURCE else "active" if outcome==CONTINUES else None,
            "planned_event_type":"expired" if outcome==EXPIRES_AFTER_SOURCE else None,
            "source_salary":str(source_ob.get("salary")) if source_ob else None,"target_salary":str(target_ob.get("salary")) if target_ob else None}
        classes.append(item)
        if outcome==EXPIRES_AFTER_SOURCE and source_ob:
            roster=roster_by_player.get(str(a.get("player_id")))
            candidates.append({"player_id":str(a.get("player_id")),"sleeper_player_id":str(a.get("sleeper_player_id")),
                "player_name":legacy.get("player_name") if legacy else None,"league_team_id":str(a.get("league_team_id")),
                "agreement_id":aid,"source_contract_season_id":str(source_ob.get("id")),"source_salary":str(source_ob.get("salary")),
                "contract_type":a.get("contract_type"),"expiration_reason":"natural_end_of_term",
                "captured_roster_state":roster.get("roster_designation") if roster else None,"remains_on_captured_roster":roster is not None,
                "has_target_obligation":False,"conflicting_evidence":[],"readiness_status":"contract_expiration_only_roster_action_not_planned"})
            if roster is None:warnings.append(_issue("expiring_not_on_captured_roster",f"Expiring player {a.get('player_id')} is absent from captured roster."))
    invalid_outcomes={INVALID_MISSING_SOURCE,INVALID_GAP,INVALID_DUPLICATE}
    if any(int(x.get("season") or 0)==2027 for x in contract_seasons):
        season_2027=next((x for x in league_seasons if str(x.get("league_id"))==league_id and int(x.get("season") or 0)==2027),None)
        if season_2027 and not season_2027.get("sleeper_league_id"):
            warnings.append(_issue("future_sleeper_metadata_missing","2027 obligations exist but the 2027 season has no Sleeper league metadata."))
    team_projections=[]
    for tid,team in sorted(team_by_id.items()):
        own=[x for x in classes if x["league_team_id"]==tid]
        def total(field,subset):return sum((Decimal(str(x[field])) for x in subset if x.get(field) is not None),Decimal("0.00"))
        exp=[x for x in own if x["outcome"]==EXPIRES_AFTER_SOURCE]; cont=[x for x in own if x["outcome"]==CONTINUES]
        y2027=[x for x in contract_seasons if str(x.get("league_team_id"))==tid and int(x["season"])==2027]
        team_projections.append({"league_team_id":tid,"team_name":team.get("owner_name") or team.get("team_name"),
            "source_active_contract_count":len([x for x in own if x.get("source_salary") is not None]),"source_active_salary":str(total("source_salary",own)),
            "expiring_count":len(exp),"expiring_salary":str(total("source_salary",exp)),"continuing_count":len(cont),
            "target_scheduled_salary":str(total("target_salary",cont)),"season_2027_scheduled_salary":str(sum((Decimal(str(x["salary"])) for x in y2027),Decimal("0.00"))),
            "projected_salary_reduction":str(total("source_salary",exp)),"unresolved_contract_errors":len([x for x in own if x["outcome"] in invalid_outcomes]),
            "cap_adjustments_reported_separately":len([x for x in cap_adjustments if str(x.get("league_team_id") or x.get("team_id"))==tid])})
    counts={"legacy_contracts":len(legacy_contracts),"agreements":len(agreements),"contract_seasons":len(contract_seasons),
        "continues":sum(x["outcome"]==CONTINUES for x in classes),"expires":sum(x["outcome"]==EXPIRES_AFTER_SOURCE for x in classes),
        "invalid":sum(x["outcome"] in invalid_outcomes for x in classes),"already_transitioned":sum(x["outcome"]==ALREADY_TRANSITIONED for x in classes),
        "source_obligations":sum(int(x["season"])==source_season for x in contract_seasons),"target_obligations":sum(int(x["season"])==target_season for x in contract_seasons),
        "season_2027_obligations":sum(int(x["season"])==2027 for x in contract_seasons),"planned_satisfied":sum(x.get("planned_source_obligation_status")=="satisfied" for x in classes),
        "planned_active":sum(x.get("planned_target_obligation_status")=="active" for x in classes),"planned_expired_agreements":sum(x.get("planned_agreement_status")=="expired" for x in classes),
        "planned_expiration_events":sum(x.get("planned_event_type")=="expired" for x in classes),"free_agent_candidates":len(candidates)}
    plan_core={"request":{"league_id":league_id,"source":source_season,"target":target_season,"planner_version":request.planner_version},
        "classifications":classes,"candidates":candidates,"team_projections":team_projections,"counts":counts}
    plan_fp=stable_fingerprint(plan_core)
    return TransitionPlan(request,tuple(classes),tuple(candidates),tuple(team_projections),tuple(warnings),tuple(errors),fingerprints,source_fp,plan_fp,
        f"contract-transition-plan:{league_id}:{source_season}:{target_season}:{request.planner_version}",counts)


def _issue(code,message,**context):return {"code":code,"message":message,"context":context}
