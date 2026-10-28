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
from pydantic import BaseModel
from supabase import Client, create_client

from api import front_office, league, market, teams
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
        "season_id": season.id,
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


def _team_page(client: Client, ctx: dict[str, Any], team_id: str) -> dict[str, Any]:
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


@app.get("/teams/{team_id}")
def team_page(team_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    return _team_page(client, _season_context(client, user), team_id)


@app.get("/my-team")
def my_team(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    if not ctx["team_id"]:
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    return _team_page(client, ctx, ctx["team_id"])


@app.get("/players/{player_id}")
def player_page(player_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    try:
        state = teams.load_team_state(client, ctx["league_id"], ctx["season"])
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load league contracts. Try again.")
    weeks = league.last_scored_week(ctx["sleeper_id"])
    result = teams.player_detail(
        player_id, state, teams.player_info(client, [player_id]).get(player_id, {}),
        teams.weekly_points(ctx["sleeper_id"], weeks, player_id), ctx["team_id"],
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Player not found.")
    return {"season": ctx["season"], **result}


class IrRequest(BaseModel):
    player_id: str


def _my_state(client: Client, ctx: dict[str, Any]) -> dict[str, Any]:
    if not ctx["team_id"]:
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    try:
        return teams.load_team_state(client, ctx["league_id"], ctx["season"], ctx["team_id"])
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load your contracts. Try again.")


@app.post("/my-team/ir")
def place_on_ir(body: IrRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Move one of your players to IR, through the same checked database call the website uses."""
    client, user = owner
    ctx = _season_context(client, user)
    state = _my_state(client, ctx)
    if teams.ir_rows(state, ctx["team_id"]):
        raise HTTPException(status_code=409, detail="Your IR spot is taken. Remove that player first.")
    row = next((r for r in state.get("roster", []) if str(r.get("sleeper_player_id")) == body.player_id), None)
    if row is None or teams.roster_designation(row):
        raise HTTPException(status_code=400, detail="That player can't go on IR.")
    try:
        client.rpc("persist_injured_reserve_assignment_authenticated", {"p_request": {
            "league_id": ctx["league_id"],
            "player_id": body.player_id,
            "league_team_id": ctx["team_id"],
            "league_season_id": ctx["season_id"],
            "normal_annual_charge": float(row.get("contract_cap_hit") or row.get("cap_hit") or 0),
        }}).execute()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"IR move was not saved: {exc}")
    return {"ok": True}


@app.delete("/my-team/ir")
def remove_from_ir(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    rows = teams.ir_rows(_my_state(client, ctx), ctx["team_id"])
    if not rows:
        raise HTTPException(status_code=404, detail="Nobody is on your IR.")
    # Deleted as the owner, so the database's team check applies. A blocked
    # delete removes nothing without an error, so confirm a row came back.
    deleted = (
        client.table("cap_adjustments").delete()
        .eq("id", rows[0]["id"]).eq("adjustment_type", "ir_adjustment")
        .execute().data or []
    )
    if not deleted:
        raise HTTPException(status_code=403, detail="IR player was not removed.")
    return {"ok": True}


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
    final = week <= league.last_scored_week(ctx["sleeper_id"])
    ttl = 86400 if final else league.LIVE_TTL
    ids = teams.starter_ids(ctx["sleeper_id"], week, matchup_id, ttl)
    result = teams.box_score(ctx["sleeper_id"], week, matchup_id, ctx["names"], ctx["slots"], teams.player_info(client, ids), ttl)
    if result is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    teams_by_roster = {t["roster_id"]: t["team_id"] for t in teams.league_teams(client, ctx["league_id"])}
    for side in result["teams"]:
        side["team_id"] = teams_by_roster.get(side["roster_id"])
    result["my_roster_id"] = ctx["sleeper_roster_id"]
    result["is_final"] = final
    return result


def _season_state(client: Client, ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        return teams.load_team_state(client, ctx["league_id"], ctx["season"])
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load league contracts. Try again.")


@app.get("/market/free-agents")
def free_agents(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    state = _season_state(client, ctx)
    try:
        pool = market.player_pool(client)
        ppg = market.ranking(client, ctx["season"])
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load the player pool. Try again.")
    return market.free_agents(pool, state, ppg)


EXPIRING_YEARS = 3


@app.get("/market/expiring")
def expiring(year: int | None = None, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Contracts ending after `year` (this season by default, up to two seasons ahead)."""
    client, user = owner
    ctx = _season_context(client, user)
    years = [ctx["season"] + i for i in range(EXPIRING_YEARS)]
    year = year if year in years else years[0]
    players = market.expiring(_season_state(client, ctx), ctx["team_id"], year - ctx["season"])
    return {"season": year, "years": years, "players": players}


# ---- Front Office ------------------------------------------------------------------


class ChatRequest(BaseModel):
    question: str
    conversation_id: str | None = None


def _front_office(client: Client, user: Any) -> front_office.Session:
    ctx = _season_context(client, user)
    if not ctx.get("team_id"):
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    return front_office.Session(client, front_office.owner_from(ctx, user), ctx["sleeper_id"])


@app.get("/front-office")
def front_office_home(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    session = _front_office(*owner)
    try:
        return front_office.home(session)
    except Exception as exc:
        print(f"APP_FRONT_OFFICE_ERROR {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail="Couldn't load the Front Office. Try again.")


@app.post("/front-office/chat")
def front_office_chat(body: ChatRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question first.")
    if len(question) > 2000:
        raise HTTPException(status_code=400, detail="That question is too long. Keep it under 2,000 characters.")
    if not front_office.ai_ready():
        raise HTTPException(status_code=503, detail="The Assistant GM isn't switched on for the app yet.")
    session = _front_office(*owner)
    if not front_office.take_question(session.owner.user_id):
        raise HTTPException(status_code=429, detail=f"That's {front_office.DAILY_QUESTIONS} questions today. The Assistant GM is back tomorrow.")
    return front_office.ask(session, question, body.conversation_id)


@app.get("/front-office/conversations")
def front_office_conversations(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    session = _front_office(*owner)
    return {"conversations": front_office.conversations(session.client, session.owner)}


@app.get("/front-office/conversations/{conversation_id}")
def front_office_conversation(conversation_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    session = _front_office(*owner)
    result = front_office.conversation(session.client, session.owner, conversation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return result
