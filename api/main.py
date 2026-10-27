"""Legacy API: the server the iPhone app talks to.

The app only holds the public Supabase key. Anything that needs the Python
league logic (standings, matchups, the Front Office AI) or a secret key runs
here instead. Every request carries the owner's Supabase access token, and
reads run as that owner so row-level security applies just like the website.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from supabase import Client, create_client

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """The owner's league and team, resolved like services/app_context.resolve_my_team."""
    client, user = owner
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

    return {
        "league_id": league_id,
        "team_id": (team or {}).get("id"),
        "team_name": (team or {}).get("team_name") or membership.get("team_name") or (team or {}).get("owner_name"),
        "owner_name": (team or {}).get("owner_name") or owner_name,
        "role": membership.get("role"),
    }
