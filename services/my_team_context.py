from __future__ import annotations

from typing import Any


class MyTeamContextError(RuntimeError): pass


def resolve_my_team(client: Any, *, user_id: str, league_id: str) -> dict | None:
    """Resolve a signed-in user through the canonical membership-to-franchise link."""
    if not user_id or not league_id: return None
    memberships = (client.table("league_memberships").select("id,league_id,user_id,role,league_team_id")
                   .eq("league_id", league_id).eq("user_id", user_id).execute().data or [])
    if not memberships: return None
    if len(memberships) != 1:
        raise MyTeamContextError(f"Expected one membership for user {user_id!r} in league {league_id!r}; found {len(memberships)}.")
    membership = memberships[0]
    team_id = membership.get("league_team_id")
    if not team_id:
        raise MyTeamContextError("Membership has no canonical league_team_id; legacy display-name authorization is disabled.")
    teams = (client.table("league_teams").select("id,league_id,team_name,owner_name,sleeper_roster_id,sleeper_user_id")
             .eq("id", team_id).eq("league_id", league_id).execute().data or [])
    if len(teams) != 1:
        raise MyTeamContextError(f"Canonical league_team_id {team_id!r} did not resolve inside league {league_id!r}.")
    team = teams[0]
    return {"league_id": league_id, "team_id": team["id"],
            "team_name": team.get("team_name") or team.get("owner_name"),
            "owner_name": team.get("owner_name") or team.get("team_name"),
            "sleeper_roster_id": team.get("sleeper_roster_id"), "role": membership.get("role")}
