"""Shared authenticated canonical roster, cap, dead-cap, and activity reads."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, MutableMapping

class CanonicalTeamStateError(RuntimeError): pass
def _text(value: Any)->str:return str(value or "").strip()
def _money(value: Any)->Decimal:
    try:return Decimal(str(value or 0))
    except (InvalidOperation,ValueError,TypeError):return Decimal("0")

def load_team_state(client:Any,league_id:str,season:int,league_team_id:str|None=None)->dict[str,Any]:
    if client is None or not _text(league_id):raise CanonicalTeamStateError("Canonical team state requires an authenticated client and league")
    request={"league_id":str(league_id),"season":int(season)}
    if _text(league_team_id):request["league_team_id"]=str(league_team_id)
    try:payload=client.rpc("read_canonical_team_state_authenticated",{"p_request":request}).execute().data
    except Exception as exc:raise CanonicalTeamStateError("Canonical team state authorization/read failed") from exc
    if not isinstance(payload,Mapping) or payload.get("schema")!="canonical-team-state-v1":raise CanonicalTeamStateError("Canonical team state returned an invalid payload")
    if str(payload.get("league_id"))!=str(league_id) or int(payload.get("season",0))!=int(season):raise CanonicalTeamStateError("Canonical team state returned mismatched authority")
    result=dict(payload)
    for key in("teams","roster","dead_cap","activity","cap_adjustments"):
        if not isinstance(result.get(key),list):raise CanonicalTeamStateError(f"Canonical team state omitted {key}")
    return result

def _normalized_roster(rows):
    return[{**r,"owner":r.get("owner_name"),"owner_team_name":r.get("team_name"),"player":r.get("player_name"),"player_position":r.get("pos"),"sleeper_id":r.get("sleeper_player_id"),"salary":r.get("cap_hit",r.get("salary")),"years":r.get("contract_years_left")}for r in rows]
def _normalized_activity(rows):
    return[{**r,"owner":r.get("owner_name"),"player":r.get("player_name"),"tx_type":r.get("action"),"acquisition":"added" if r.get("action")=="add" else "dropped","ts":r.get("effective_at")or r.get("created_at"),"_source":"contract_events","_canonical_event_id":r.get("id")}for r in rows]

def state_roster(state:Mapping[str,Any])->list[dict[str,Any]]:return _normalized_roster(state["roster"])
def state_activity(state:Mapping[str,Any],limit:int=500)->list[dict[str,Any]]:return _normalized_activity(state["activity"][:limit])
def dead_cap_display_rows(rows:Iterable[Mapping[str,Any]])->list[tuple[str,Decimal]]:
    """Return the only two values authorized for Dead Cap presentation."""
    return [(_text(row.get("player_name")) or "Player", _money(row.get("amount"))) for row in rows if row.get("adjustment_type")=="dropped_player_charge"]
def state_cap_adjustments(state:Mapping[str,Any])->list[dict[str,Any]]:
    canonical=list(state["dead_cap"]);keys={(str(r.get("league_team_id")),_text(r.get("player_id")).casefold(),int(r.get("season")or 0))for r in canonical};merged=list(canonical)
    for raw in state["cap_adjustments"]:
        row=dict(raw);key=(str(row.get("league_team_id")),_text(row.get("sleeper_player_id")).casefold(),int(row.get("season")or 0))
        if row.get("adjustment_type")=="dropped_player_charge" and key in keys:continue
        merged.append(row)
    return merged
def load_canonical_roster(client,league_id,season,league_team_id=None):return state_roster(load_team_state(client,league_id,season,league_team_id))
def load_canonical_dead_cap(client,league_id,season,league_team_id=None):return list(load_team_state(client,league_id,season,league_team_id)["dead_cap"])
def load_team_cap_adjustments(client,league_id,season,league_team_id=None):return state_cap_adjustments(load_team_state(client,league_id,season,league_team_id))
def load_canonical_activity(client,league_id,limit=500,*,season,league_team_id=None):return state_activity(load_team_state(client,league_id,season,league_team_id),limit)

def invalidate_team_state_session_cache(session_state:MutableMapping[str,Any])->int:
    value=int(session_state.get("team_state_cache_epoch",0))+1;session_state["team_state_cache_epoch"]=value;return value

def merge_activity(canonical_rows:Iterable[Mapping[str,Any]],legacy_rows:Iterable[Mapping[str,Any]])->list[dict[str,Any]]:
    canonical=[dict(r)for r in canonical_rows];keys={(str(r.get("league_team_id")or""),_text(r.get("player_id")or r.get("player_name")).casefold(),_text(r.get("action")).casefold())for r in canonical};merged=list(canonical);seen={_text(r.get("id")or r.get("idempotency_key"))for r in canonical}
    for raw in legacy_rows:
        row=dict(raw);identity=_text(row.get("id")or row.get("idempotency_key"))
        if identity and identity in seen:continue
        action=_text(row.get("action")or row.get("tx_type")or row.get("acquisition")).casefold();action="add"if action in{"add","added","sign","signed","waiver","free_agent"}else("drop"if action in{"drop","dropped","released"}else action)
        player=_text(row.get("player_id")or row.get("player_sleeper_id")or row.get("player_name")or row.get("player")).casefold();team=str(row.get("league_team_id")or"")
        if action in{"add","drop"}and any(action==k[2]and player==k[1]and(not team or team==k[0])for k in keys):continue
        merged.append(row)
        if identity:seen.add(identity)
    return merged

def calculate_team_financials(roster_rows,adjustment_rows,*,salary_cap,league_team_id=None,owner_name=None):
    team_id=_text(league_team_id);owner=_text(owner_name).casefold()
    def belongs(r):return(bool(team_id)and _text(r.get("league_team_id"))==team_id)or(bool(owner)and owner in{_text(r.get("owner")or r.get("owner_name")).casefold(),_text(r.get("team_name")or r.get("owner_team_name")).casefold()})
    roster=[r for r in roster_rows if belongs(r)];adjustments=[r for r in adjustment_rows if belongs(r)];active=sum((_money(r.get("cap_hit",r.get("salary")))for r in roster),Decimal("0"));total=sum((_money(r.get("amount"))for r in adjustments),Decimal("0"));dead=sum((_money(r.get("amount"))for r in adjustments if r.get("adjustment_type")=="dropped_player_charge"),Decimal("0"));cap=_money(salary_cap);used=active+total
    return{"active_salary":active,"dead_cap":dead,"adjustments":total,"cap_used":used,"cap_space":cap-used}
