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

from api import league, teams
from season_engine import SeasonResolver

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


def _team_ids(client: Client, user: Any) -> dict[int, str]:
    """roster id -> league team id, so the app can open a team page from any row."""
    team = resolve_team(client, user)
    return {t["roster_id"]: t["team_id"] for t in teams.league_teams(client, team["league_id"])}


@app.get("/standings")
def standings(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    sleeper_id, names, my_roster_id = _league_context(*owner)
    result = league.standings(sleeper_id, names, my_roster_id)
    ids = _team_ids(*owner)
    for row in result["rows"]:
        row["team_id"] = ids.get(row["roster_id"])
    return result


@app.get("/matchups")
def matchups(
    week: int | None = Query(default=None, ge=1, le=25),
    owner: tuple[Client, Any] = Depends(owner_client),
) -> dict[str, Any]:
    sleeper_id, names, my_roster_id = _league_context(*owner)
    result = league.matchups(sleeper_id, names, week or league.current_week(), my_roster_id)
    result["current_week"] = league.current_week()
    return result


def _season_context(client: Client, user: Any) -> dict[str, Any]:
    """Everything the team pages share: who I am, the season, and Sleeper ids."""
    team = resolve_team(client, user)
    season = SeasonResolver(client).get_active_season(team["league_id"])
    if not season.sleeper_league_id:
        raise HTTPException(status_code=409, detail="The active season isn't linked to a Sleeper league yet.")
    sleeper_id = season.sleeper_league_id
    return {
        **team,
        "season": int(season.season),
        "sleeper_id": sleeper_id,
        "names": league.roster_names(client, team["league_id"], sleeper_id),
        "slots": (league.sleeper.get(f"league/{sleeper_id}") or {}).get("roster_positions") or [],
    }


def _league_teams(client: Client, ctx: dict[str, Any]) -> tuple[list[dict], list[dict], list[dict], float]:
    standings_rows = league.standings(ctx["sleeper_id"], ctx["names"], ctx["sleeper_roster_id"])["rows"]
    cap = teams.salary_cap(client, ctx["league_id"])
    try:
        states = teams.seasons_state(client, ctx["league_id"], ctx["season"])
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load league contracts. Try again.")
    rows = teams.summaries(
        teams.league_teams(client, ctx["league_id"]), states, cap, standings_rows,
        teams.team_leans(client, ctx["league_id"]),
    )
    return rows, states, standings_rows, cap


@app.get("/teams")
def team_list(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    rows, _, _, cap = _league_teams(client, ctx)
    return {"season": ctx["season"], "salary_cap": cap, "my_team_id": ctx["team_id"], "teams": rows}


@app.get("/teams/{team_id}")
def team_page(team_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    rows, states, standings_rows, cap = _league_teams(client, ctx)
    summary = next((t for t in rows if t["team_id"] == team_id), None)
    if summary is None:
        raise HTTPException(status_code=404, detail="Team not found in your league.")

    state = states[0]
    weeks = league.last_scored_week(ctx["sleeper_id"])
    ids = [r.get("sleeper_player_id") for r in state.get("roster", []) if str(r.get("league_team_id")) == team_id]
    standing = next((r for r in standings_rows if r["roster_id"] == summary["roster_id"]), None)
    detail = teams.team_detail(
        team={k: summary[k] for k in ("team_id", "owner_name", "team_name", "roster_id")},
        summary=summary,
        standing=standing,
        state=state,
        sleeper_id=ctx["sleeper_id"],
        slots=ctx["slots"],
        info=teams.player_info(client, ids),
        ppg=teams.player_ppg(ctx["sleeper_id"], weeks),
        h2h=teams.head_to_head(ctx["sleeper_id"], weeks, ctx["sleeper_roster_id"], summary["roster_id"]),
    )
    return {"season": ctx["season"], "salary_cap": cap, "sleeper_league_id": ctx["sleeper_id"], **detail}


@app.get("/matchups/{week}/{matchup_id}")
def box_score(
    week: int,
    matchup_id: int,
    owner: tuple[Client, Any] = Depends(owner_client),
) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    if not 1 <= week <= 25:
        raise HTTPException(status_code=404, detail="No such week.")
    ttl = 86400 if week <= league.last_scored_week(ctx["sleeper_id"]) else 60
    ids = teams.starter_ids(ctx["sleeper_id"], week, matchup_id, ttl)
    result = teams.box_score(ctx["sleeper_id"], week, matchup_id, ctx["names"], ctx["slots"], teams.player_info(client, ids), ttl)
    if result is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    teams_by_roster = {t["roster_id"]: t["team_id"] for t in teams.league_teams(client, ctx["league_id"])}
    for side in result["teams"]:
        side["team_id"] = teams_by_roster.get(side["roster_id"])
    result["my_roster_id"] = ctx["sleeper_roster_id"]
    return result
