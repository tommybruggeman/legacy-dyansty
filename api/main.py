"""Legacy API: the server the iPhone app talks to.

The app only holds the public Supabase key. Anything that needs the Python
league logic (standings, matchups, the Front Office AI) or a secret key runs
here instead. Every request carries the owner's Supabase access token, and
reads run as that owner so row-level security applies just like the website.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from supabase import Client, create_client

from api import league

app = FastAPI(title="Legacy API")


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} in environment.")
    return value


def owner_client(authorization: str = Header(default="")) -> tuple[Client, Any]:
    """Supabase client acting as the signed-in owner, plus their user."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Sign in required.")

    client = create_client(_env("SUPABASE_URL"), _env("SUPABASE_ANON_KEY"))
    try:
        user = client.auth.get_user(token).user
    except Exception:
        user = None
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")

    client.postgrest.auth(token)
    return client, user


def _first(rows: list[dict] | None) -> dict | None:
    return (rows or [None])[0]


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "Legacy API", "status": "running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def resolve_team(client: Client, user: Any) -> dict[str, Any]:
    """The owner's league and team, resolved like services/app_context.resolve_my_team."""
    membership = _first(
        client.table("league_memberships").select("*").eq("user_id", user.id).limit(1).execute().data
    )
    if not membership:
        raise HTTPException(status_code=404, detail="This account isn't linked to a league yet.")

    league_id = membership.get("league_id")
    team_id = membership.get("league_team_id") or membership.get("team_id")
    owner_name = membership.get("owner_name") or membership.get("sleeper_username")

    team = None
    query = client.table("league_teams").select("*").eq("league_id", league_id).limit(1)
    if team_id:
        team = _first(query.eq("id", team_id).execute().data)
    elif owner_name:
        team = _first(query.eq("owner_name", owner_name).execute().data)
    team = team or {}

    try:
        roster_id = int(team.get("sleeper_roster_id"))
    except (TypeError, ValueError):
        roster_id = None

    return {
        "league_id": league_id,
        "team_id": team.get("id"),
        "team_name": team.get("team_name") or membership.get("team_name") or team.get("owner_name"),
        "owner_name": team.get("owner_name") or owner_name,
        "role": membership.get("role"),
        "sleeper_roster_id": roster_id,
    }


@app.get("/me")
def me(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    return resolve_team(client, user)


def _league_context(client: Client, user: Any) -> tuple[str, dict[int, str], int | None]:
    team = resolve_team(client, user)
    try:
        sleeper_id = league.sleeper_league_id(client, team["league_id"])
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return sleeper_id, league.roster_names(client, team["league_id"], sleeper_id), team["sleeper_roster_id"]


@app.get("/standings")
def standings(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    sleeper_id, names, my_roster_id = _league_context(*owner)
    return league.standings(sleeper_id, names, my_roster_id)


@app.get("/matchups")
def matchups(
    week: int | None = Query(default=None, ge=1, le=25),
    owner: tuple[Client, Any] = Depends(owner_client),
) -> dict[str, Any]:
    sleeper_id, names, my_roster_id = _league_context(*owner)
    result = league.matchups(sleeper_id, names, week or league.current_week(), my_roster_id)
    result["current_week"] = league.current_week()
    return result
