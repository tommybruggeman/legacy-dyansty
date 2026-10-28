"""Players on a Sleeper roster that the app's league state leaves out.

The canonical team state lists a player only while he has an active contract for the season. A player whose
contract ran out (or who never got one) can still sit on a Sleeper roster, and without him the Assistant GM,
the power rankings and the playoff odds plan as if he were gone ("you have 8 players"). `fill_from_sleeper`
adds those players to the roster at $0 and marks them NO_CONTRACT, so lineups count them, cap totals stay the
app's own, and the pack can say their contract is missing. Only the AI and Front Office use the filled state;
the cap and contract pages keep reading the canonical state.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Mapping

import requests

SLEEPER = "https://api.sleeper.app/v1"
NO_CONTRACT = "no_contract"


def is_no_contract(row: Mapping[str, Any]) -> bool:
    return str(row.get("status") or "") == NO_CONTRACT


def missing_rows(state: Mapping[str, Any], sleeper_rosters: list[Mapping[str, Any]], roster_to_team: Mapping[int, str],
                 players: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Roster rows for players on a Sleeper roster with no row in the state."""
    have = {str(r.get("sleeper_player_id") or "") for r in state.get("roster") or []}
    teams = {str(t.get("league_team_id")): t for t in state.get("teams") or []}
    out = []
    for roster in sleeper_rosters:
        team_id = roster_to_team.get(int(roster.get("roster_id") or 0))
        team = teams.get(str(team_id)) if team_id else None
        if not team:
            continue
        taxi = {str(p) for p in roster.get("taxi") or []}
        ir = {str(p) for p in roster.get("reserve") or []}
        for pid in [str(p) for p in roster.get("players") or [] if p] + sorted(taxi - {str(p) for p in roster.get("players") or []}):
            if pid in have:
                continue
            have.add(pid)
            p = players.get(pid) or {}
            out.append({
                "contract_agreement_id": None, "league_id": state.get("league_id"), "league_team_id": str(team_id), "player_id": None,
                "sleeper_player_id": pid, "player_name": p.get("full_name") or pid, "pos": p.get("position") or "",
                "owner_name": team.get("owner_name"), "team_name": team.get("team_name") or team.get("owner_name"),
                "salary": Decimal("0"), "cap_hit": Decimal("0"), "contract_type": None, "status": NO_CONTRACT,
                "season": state.get("season"), "contract_years_left": 0,
                "roster_designation": "taxi" if pid in taxi else "ir" if pid in ir else None,
            })
    return out


def fill_from_sleeper(client: Any, state: Mapping[str, Any], sleeper_league_id: str | None, league_id: str,
                      get_json: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """The state with every Sleeper-rostered player in it. Never raises: on any failure the state comes back as it was."""
    if not sleeper_league_id:
        return dict(state)
    get_json = get_json or (lambda url: requests.get(url, timeout=15).json())
    try:
        rosters = get_json(f"{SLEEPER}/league/{sleeper_league_id}/rosters") or []
        teams = client.table("league_teams").select("id, sleeper_roster_id").eq("league_id", league_id).execute().data or []
        roster_to_team = {int(t["sleeper_roster_id"]): str(t["id"]) for t in teams if t.get("sleeper_roster_id") is not None}
        have = {str(r.get("sleeper_player_id") or "") for r in state.get("roster") or []}
        ids = sorted({str(p) for r in rosters for k in ("players", "taxi", "reserve") for p in r.get(k) or [] if p} - have)
        players: dict[str, Mapping[str, Any]] = {}
        for i in range(0, len(ids), 150):
            for row in client.table("nfl_players").select("sleeper_id, full_name, position").in_("sleeper_id", ids[i:i + 150]).execute().data or []:
                players[str(row["sleeper_id"])] = row
        extra = missing_rows(state, rosters, roster_to_team, players)
    except Exception as exc:
        print(f"SLEEPER_ROSTER_FILL_ERROR {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return dict(state)
    return {**state, "roster": [*state.get("roster", []), *extra]} if extra else dict(state)
