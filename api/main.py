"""Legacy API: the server the iPhone app talks to.

The app only holds the public Supabase key. Anything that needs the Python
league logic (standings, matchups, the Front Office AI) or a secret key runs
here instead. Every request carries the owner's Supabase access token, and
reads run as that owner so row-level security applies just like the website.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from supabase import Client, create_client

from api import box_score as box_score_mod
from api import cache, front_office, home, league, market, player, projections, teams
from season_engine import SeasonResolver
from services.season_rollover_owner_ui import OwnerRolloverDecisionError, OwnerRolloverDecisionService

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
    # Checking a token is a round trip to Supabase Auth; a checked token is
    # trusted for a minute (tokens live an hour and the app refreshes them).
    try:
        user = cache.cached(("user", cache.token_key(token)), 60, lambda: client.auth.get_user(token).user)
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
    return cache.cached(("team", user.id), 600, lambda: _resolve_team(client, user))


def _resolve_team(client: Client, user: Any) -> dict[str, Any]:
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
    return sleeper_id, _roster_names(client, team["league_id"], sleeper_id), team["sleeper_roster_id"]


def _team_ids(client: Client, user: Any) -> dict[int, str]:
    """roster id -> league team id, so the app can open a team page from any row."""
    team = resolve_team(client, user)
    return {t["roster_id"]: t["team_id"] for t in _league_team_rows(client, team["league_id"])}


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
    if not result["is_final"]:
        try:
            _add_card_projections(*owner, result)
        except Exception as exc:
            print(f"APP_MATCHUP_PROJ_ERROR {type(exc).__name__}: {exc}", flush=True)
    return result


def _add_card_projections(client: Client, user: Any, result: dict[str, Any]) -> None:
    """Each side's projected total on the week's game cards, scored the same way as the box score."""
    ctx = _season_context(client, user)
    week = result["week"]
    rows = league.sleeper.get(f"league/{ctx['sleeper_id']}/matchups/{week}", ttl=league.LIVE_TTL) or []
    sides = []
    for row in rows:
        points = row.get("players_points") or {}
        starters = [str(pid) for pid in row.get("starters") or [] if pid and str(pid) != "0"]
        sides.append({"roster_id": row.get("roster_id"), "players": [{"player_id": pid, "points": float(points.get(pid) or 0)} for pid in starters]})
    info = teams.player_info(client, [p["player_id"] for side in sides for p in side["players"]])
    _add_projections(client, ctx, week, False, {"teams": sides}, info)
    projected = {side["roster_id"]: side["projected"] for side in sides}
    for game in result["games"]:
        for team in game["teams"]:
            team["projected"] = projected.get(team["roster_id"])


def _season_context(client: Client, user: Any) -> dict[str, Any]:
    """Everything the team pages share: who I am, the season, and Sleeper ids."""
    team = resolve_team(client, user)
    season = cache.cached(("season", team["league_id"]), 600, lambda: SeasonResolver(client).get_active_season(team["league_id"]))
    if not season.sleeper_league_id:
        raise HTTPException(status_code=409, detail="The active season isn't linked to a Sleeper league yet.")
    sleeper_id = season.sleeper_league_id
    return {
        **team,
        "season": int(season.season),
        "season_id": season.id,
        "sleeper_id": sleeper_id,
        "names": _roster_names(client, team["league_id"], sleeper_id),
        "slots": (league.sleeper.get(f"league/{sleeper_id}") or {}).get("roster_positions") or [],
    }


def _roster_names(client: Client, league_id: str, sleeper_id: str) -> dict[int, str]:
    return cache.cached(("names", league_id, sleeper_id), 600, lambda: league.roster_names(client, league_id, sleeper_id))


def _league_team_rows(client: Client, league_id: str) -> list[dict[str, Any]]:
    return cache.cached(("league_teams", league_id), 600, lambda: teams.league_teams(client, league_id))


def _seasons_state(client: Client, league_id: str, season: int) -> list[dict[str, Any]]:
    """Contracts for this season and the next two, shared by the league for a minute."""
    return cache.cached(("state", league_id, season), 60, lambda: teams.seasons_state(client, league_id, season))


def _league_teams(client: Client, ctx: dict[str, Any]) -> tuple[list[dict], list[dict], list[dict], float]:
    lid = ctx["league_id"]
    # These reads don't depend on each other, so they run side by side.
    jobs = {
        "standings": cache.pool.submit(league.standings, ctx["sleeper_id"], ctx["names"], ctx["sleeper_roster_id"]),
        "cap": cache.pool.submit(cache.cached, ("cap", lid), 600, lambda: teams.salary_cap(client, lid)),
        "states": cache.pool.submit(_seasons_state, client, lid, ctx["season"]),
        "teams": cache.pool.submit(_league_team_rows, client, lid),
        "leans": cache.pool.submit(cache.cached, ("leans", lid), 600, lambda: teams.team_leans(client, lid)),
    }
    try:
        states = jobs["states"].result()
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load league contracts. Try again.")
    standings_rows, cap = jobs["standings"].result()["rows"], jobs["cap"].result()
    rows = teams.summaries(jobs["teams"].result(), states, cap, standings_rows, jobs["leans"].result())
    return rows, states, standings_rows, cap


@app.get("/teams")
def team_list(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    rows, _, _, cap = _league_teams(client, ctx)
    return {"season": ctx["season"], "salary_cap": cap, "my_team_id": ctx["team_id"], "teams": rows}


def _saved_power_rank(client: Client, ctx: dict[str, Any], team_id: str) -> dict[str, Any] | None:
    """Latest power rank the Front Office saved (no model run here) and its move over about a week."""
    def load() -> dict[str, Any]:
        rows = (client.table("league_power_rankings").select("computed_on,league_team_id,now_rank")
                .eq("league_id", ctx["league_id"]).order("computed_on", desc=True).limit(400).execute().data or [])
        return {"rows": rows}
    try:
        rows = cache.cached(("power_rank", ctx["league_id"]), 600, load)["rows"]
    except Exception:
        return None
    mine = [r for r in rows if str(r.get("league_team_id")) == str(team_id) and r.get("now_rank") is not None]
    if not mine:
        return None
    latest = rows[0]["computed_on"]
    teams_ranked = sum(1 for r in rows if r["computed_on"] == latest)
    now = mine[0]
    older = next((r for r in mine if (date.fromisoformat(str(now["computed_on"])) - date.fromisoformat(str(r["computed_on"]))).days >= 7), None)
    move = int(older["now_rank"]) - int(now["now_rank"]) if older else None
    return {"rank": int(now["now_rank"]), "of": teams_ranked or None, "move": move}


def _team_page(client: Client, ctx: dict[str, Any], team_id: str) -> dict[str, Any]:
    rows, states, standings_rows, cap = _league_teams(client, ctx)
    summary = next((t for t in rows if t["team_id"] == team_id), None)
    if summary is None:
        raise HTTPException(status_code=404, detail="Team not found in your league.")

    state = states[0]
    weeks = league.last_scored_week(ctx["sleeper_id"])
    ids = [r.get("sleeper_player_id") for r in state.get("roster", []) if str(r.get("league_team_id")) == team_id]
    try:  # plus anyone on his Sleeper roster without a contract row yet
        ids += [pid for pid, rid in league.sleeper_owners(ctx["sleeper_id"]).items() if rid == summary["roster_id"]]
    except Exception:
        pass
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
        later=states[1:],
        weeks=league.current_week(),
        power=_saved_power_rank(client, ctx, team_id),
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
        state = _seasons_state(client, ctx["league_id"], ctx["season"])[0]
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load league contracts. Try again.")
    weeks = league.last_scored_week(ctx["sleeper_id"])
    meta = teams.player_info(client, [player_id]).get(player_id, {})
    league_weeks = teams.weekly_points(ctx["sleeper_id"], weeks, player_id)
    if any(w["points"] is None for w in league_weeks) and meta.get("position"):
        try:
            scores = player.weekly_scores(ctx["sleeper_id"], ctx["season"], weeks, player_id, str(meta["position"]))
            league_weeks = player.with_unrostered_points(league_weeks, scores)
        except Exception:
            pass
    result = teams.player_detail(player_id, state, meta, league_weeks, ctx["team_id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Player not found.")
    c = result.get("contract")
    extras = player.page_extras(
        client, league_id=ctx["league_id"], sleeper_id=ctx["sleeper_id"], season=ctx["season"], player_id=player_id,
        meta=meta, pos=result["pos"], league_weeks=league_weeks, last_week=weeks,
        years_left={c["team_id"]: c["years_left"]} if c else {}, salary=c["salary"] if c else None,
        player_info=teams.player_info,
    )
    if c is None:
        # No contract row, but Sleeper may still have him on a roster (a new rookie pick, a pickup not synced yet).
        try:
            rid = league.sleeper_owners(ctx["sleeper_id"]).get(player_id)
        except Exception:
            rid = None
        if rid is not None:
            team_id = next((t["team_id"] for t in _league_team_rows(client, ctx["league_id"]) if t["roster_id"] == rid), None)
            result["rostered_by"] = {"roster_id": rid, "owner_name": ctx["names"].get(rid, f"Roster {rid}"), "team_id": team_id}
            result["is_mine"] = rid == ctx["sleeper_roster_id"]
    return {"season": ctx["season"], **result, **extras}


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
    cache.forget("state", ctx["league_id"])
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
    cache.forget("state", ctx["league_id"])
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
    result = box_score_mod.game(client, ctx, week, matchup_id, _league_team_rows(client, ctx["league_id"]))
    if result is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    return result


def _season_state(client: Client, ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        return _seasons_state(client, ctx["league_id"], ctx["season"])[0]
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load league contracts. Try again.")


def _market_ppg(client: Client, ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:
    try:
        return market.player_pool(client), market.ranking(client, ctx["season"])
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't load the player pool. Try again.")


@app.get("/market/free-agents")
def free_agents(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    state = _season_state(client, ctx)
    pool, ppg = _market_ppg(client, ctx)
    try:
        rostered = league.sleeper_owners(ctx["sleeper_id"])
    except Exception:
        rostered = {}
    return market.free_agents(pool, state, ppg, rostered=rostered)


EXPIRING_YEARS = 3


def _league_phase(ctx: dict[str, Any]) -> tuple[str, date]:
    """The same season phase Home uses: decisions runs from the final game to the June 30 rollover."""
    sleeper_league = league.sleeper.get(f"league/{ctx['sleeper_id']}") or {}
    playoff_start = int((sleeper_league.get("settings") or {}).get("playoff_week_start") or 15)
    today = datetime.now(front_office.LEAGUE_TZ).date()
    return home.phase(today, sleeper_league.get("status"), league.current_week(), playoff_start), today


def _my_calls(client: Client, ctx: dict[str, Any], state: dict[str, Any], pool: list[dict[str, Any]], ppg: Any, today: date) -> dict[str, Any]:
    """My keep-or-walk calls before the rollover, from the same rollover service as Home."""
    rates = dict(ppg.ranking_ppg)
    try:
        loaded = OwnerRolloverDecisionService(client, ctx["league_id"]).load()
    except OwnerRolloverDecisionError:
        loaded = {"execution": None, "decisions": []}
    execution = loaded.get("execution") or {}
    players = (home.rookie_decisions(loaded.get("decisions") or [], state.get("roster", []), rates) if loaded.get("decisions")
               else home.rookie_deals_ending(state.get("roster", []), ctx["team_id"], rates))
    nfl = market.nfl_teams(pool)
    for p in players:
        p["nfl_team"] = nfl.get(p["player_id"], "FA")
    return {
        "window_open": execution.get("status") == "decision_window_open",
        "deadline": execution.get("owner_deadline") or home.rollover_deadline(today).isoformat(),
        "players": players,
    }


def _saved_ids(client: Client, league_id: str) -> list[str] | None:
    """Saved targets, or None while the market_targets table hasn't been created."""
    try:
        return market.target_ids(client, league_id)
    except Exception as exc:
        print(f"APP_MARKET_TARGETS_ERROR {type(exc).__name__}: {exc}", flush=True)
        return None


@app.get("/market/expiring")
def expiring(year: int | None = None, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Contracts ending after `year` (this season by default, up to two seasons ahead),
    plus my calls before the rollover (offseason) or my saved transfer targets."""
    client, user = owner
    ctx = _season_context(client, user)
    years = [ctx["season"] + i for i in range(EXPIRING_YEARS)]
    year = year if year in years else years[0]
    state = _season_state(client, ctx)
    pool, ppg = _market_ppg(client, ctx)
    players = market.expiring(state, ctx["team_id"], year - ctx["season"], pool=pool, ppg=ppg)
    phase, today = _league_phase(ctx)
    calls = _my_calls(client, ctx, state, pool, ppg, today) if phase == "decisions" and ctx.get("team_id") else None
    ids = _saved_ids(client, ctx["league_id"])
    saved = market.targets(ids or [], state, ctx["team_id"], pool, ppg)
    return {
        "season": year, "years": years, "players": players, "sleeper_league_id": ctx["sleeper_id"],
        "offseason": phase in ("decisions", "draft_prep"),
        "calls": calls,
        "targets": saved,
        "targets_ready": ids is not None,
        "plan_question": market.strategy_question(saved, ctx["season"]),
    }


@app.get("/market/targets")
def target_list(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ids = _saved_ids(client, resolve_team(client, user)["league_id"])
    return {"ids": ids or [], "ready": ids is not None}


def _change_target(owner: tuple[Client, Any], player_id: str, save: bool) -> dict[str, Any]:
    client, user = owner
    league_id = resolve_team(client, user)["league_id"]
    try:
        (market.save_target if save else market.drop_target)(client, league_id, player_id.strip())
    except Exception as exc:
        print(f"APP_MARKET_TARGETS_ERROR {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=503, detail="Saving targets isn't switched on yet. Try again later.")
    return {"ids": market.target_ids(client, league_id), "ready": True}


@app.post("/market/targets/{player_id}")
def add_target(player_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _change_target(owner, player_id, True)


@app.delete("/market/targets/{player_id}")
def remove_target(player_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _change_target(owner, player_id, False)


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


def _chat_session(body: ChatRequest, client: Client, user: Any) -> tuple[front_office.Session, str]:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question first.")
    if len(question) > 2000:
        raise HTTPException(status_code=400, detail="That question is too long. Keep it under 2,000 characters.")
    if not front_office.ai_ready():
        raise HTTPException(status_code=503, detail="The Assistant GM isn't switched on for the app yet.")
    session = _front_office(client, user)
    if not front_office.take_question(session.owner.user_id):
        raise HTTPException(status_code=429, detail=f"That's {front_office.DAILY_QUESTIONS} questions today. The Assistant GM is back tomorrow.")
    return session, question


@app.post("/front-office/chat")
def front_office_chat(body: ChatRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    session, question = _chat_session(body, *owner)
    return front_office.ask(session, question, body.conversation_id)


@app.post("/front-office/chat/stream")
def front_office_chat_stream(body: ChatRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> StreamingResponse:
    """The same answer as /front-office/chat, streamed: what the GM is checking, then the answer as it's written."""
    session, question = _chat_session(body, *owner)
    return StreamingResponse(front_office.ask_stream(session, question, body.conversation_id), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


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


# ---- Home --------------------------------------------------------------------------


def _brief_line(client: Client, ctx: dict[str, Any], user: Any, week: int) -> str:
    """This week's saved Front Office call, if the brief has been written (no AI call here)."""
    saved = front_office.load_brief(client, ctx["league_id"], str(user.id), ctx["season"], week) or {}
    return ((saved.get("injuries") or {}).get("call") or (saved.get("matchup") or {}).get("stakes") or "").strip()


@app.get("/home")
def home_page(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    if not ctx.get("team_id"):
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    sid, names, my_roster = ctx["sleeper_id"], ctx["names"], ctx["sleeper_roster_id"]
    sleeper_league = league.sleeper.get(f"league/{sid}") or {}
    playoff_start = int((sleeper_league.get("settings") or {}).get("playoff_week_start") or 15)
    week = league.current_week()
    today = datetime.now(front_office.LEAGUE_TZ).date()
    phase = home.phase(today, sleeper_league.get("status"), week, playoff_start)

    rows, states, standings_rows, cap = _league_teams(client, ctx)
    me = next((r for r in rows if r["team_id"] == ctx["team_id"]), None) or {}
    state = states[0]
    weeks = league.last_scored_week(sid)
    out: dict[str, Any] = {
        "phase": phase,
        "season": ctx["season"],
        "week": week,
        "today": today.isoformat(),
        "owner_name": ctx.get("owner_name") or "",
        "salary_cap": cap,
        "me": {k: me.get(k) for k in ("rank", "wins", "losses", "ties", "standing_points", "cap_space", "cap_by_year", "team_id")},
        "teams_count": len(rows),
        "playoff_teams": league.PLAYOFF_TEAMS,
        "front_office": {"line": cache.cached(("brief", user.id, week), 300, lambda: _brief_line(client, ctx, user, week)), "prompts": front_office.rotating_prompts(week=week)},
    }

    pics: dict[int, str | None] = {}
    if phase in ("game_day", "recap", "playoffs"):
        try:
            pics = home.avatars(league.sleeper.get(f"league/{sid}/users") or [], league.sleeper.get(f"league/{sid}/rosters") or [])
        except Exception:
            pics = {}
        by_roster = {r["roster_id"]: r for r in standings_rows}
        team_ids = {r["roster_id"]: r["team_id"] for r in rows}

        def side(s: dict[str, Any] | None) -> dict[str, Any] | None:
            if not s:
                return s
            st = by_roster.get(s["roster_id"], {})
            return {**s, "avatar": pics.get(s["roster_id"]), "team_id": team_ids.get(s["roster_id"]), "season_rank": st.get("rank"),
                    "wins": st.get("wins", 0), "losses": st.get("losses", 0), "ties": st.get("ties", 0), "ppg": st.get("pf_per_game")}

        def game_view(game: dict[str, Any] | None) -> dict[str, Any]:
            return {**game, "me": side(game.get("me")), "them": side(game.get("them"))} if game else {}

        def lineups(wk: int, game: dict[str, Any] | None, final: bool) -> dict[str, list[dict[str, Any]]]:
            if not game or not game.get("me"):
                return {"me": [], "them": []}
            raw = league.sleeper.get(f"league/{sid}/matchups/{wk}", ttl=86400 if final else league.LIVE_TTL) or []
            mine = next((r for r in raw if r.get("roster_id") == game["me"]["roster_id"]), None)
            theirs = next((r for r in raw if game.get("them") and r.get("roster_id") == game["them"]["roster_id"]), None)
            ids = [p for r in (mine, theirs) if r for p in (r.get("starters") or [])]
            info = teams.player_info(client, ids)
            try:
                nfl = cache.cached(("nfl_games", ctx["season"], wk), 600, lambda: client.table("nfl_games").select("gameday,gametime,home_team,away_team")
                                   .eq("season", ctx["season"]).eq("week", wk).execute().data or [])
            except Exception:
                nfl = []
            now = datetime.now(front_office.LEAGUE_TZ)
            return {"me": home.lineup(mine, info, home.kickoffs(nfl), now), "them": home.lineup(theirs, info, home.kickoffs(nfl), now)}

        current = league.matchups(sid, names, week, my_roster)
        mine_now = home.my_game(current["games"], my_roster)
        out["game"] = {"week": week, "is_final": current["is_final"], **game_view(mine_now)}
        out["weekday"] = today.weekday()
        out["standings"] = home.standings_table(standings_rows, team_ids, pics, league.PLAYOFF_TEAMS)
        if phase in ("game_day", "playoffs"):
            players = lineups(week, mine_now, current["is_final"])
            out["players"] = players
            out["lineup_issues"] = home.lineup_issues(players["me"])
            out["race"] = home.race(current["games"], my_roster, pics)
            them = (mine_now or {}).get("them")
            if them:
                out["series"] = teams.head_to_head(sid, weeks, my_roster, them["roster_id"])
    if phase == "recap" and week > 1:
        last = league.matchups(sid, names, week - 1, my_roster)
        game = home.my_game(last["games"], my_roster)
        recap_players = lineups(week - 1, game, True)["me"]
        out["recap"] = {
            "week": week - 1, "game": game_view(game), "summary": last["summary"],
            "top_scorers": sorted((p for p in recap_players if p["player_id"]), key=lambda p: -p["points"])[:3],
            "standing_points": home.standing_points_earned(game),
        }
        them = (mine_now or {}).get("them")
        if them:
            out["series"] = teams.head_to_head(sid, weeks, my_roster, them["roster_id"])
        out["weeks_left"] = max(0, playoff_start - week)
    if phase == "playoffs":
        try:
            matches = league.sleeper.get(f"league/{sid}/winners_bracket", ttl=300) or []
        except Exception:
            matches = []
        points: dict[tuple[int, int], float] = {}
        for rnd in {int(m.get("r") or 0) for m in matches if m.get("r")}:
            for game in league.matchups(sid, names, playoff_start + rnd - 1, my_roster)["games"]:
                for side in game["teams"]:
                    points[(rnd, side["roster_id"])] = side["score"]
        out["bracket"] = home.bracket(matches, names, points, playoff_start)
        for rnd in out["bracket"]:
            for g in rnd["games"]:
                for key in ("a", "b"):
                    if g[key]:
                        g[key]["avatar"] = pics.get(g[key]["roster_id"])
                        g[key]["seed"] = by_roster.get(g[key]["roster_id"], {}).get("rank")
    if phase == "decisions":
        ppg = teams.player_ppg(sid, weeks)
        try:
            loaded = OwnerRolloverDecisionService(client, ctx["league_id"]).load()
        except OwnerRolloverDecisionError:
            loaded = {"execution": None, "decisions": []}
        execution = loaded.get("execution") or {}
        window_open = execution.get("status") == "decision_window_open"
        players = (home.rookie_decisions(loaded.get("decisions") or [], state.get("roster", []), ppg) if loaded.get("decisions")
                   else home.rookie_deals_ending(state.get("roster", []), ctx["team_id"], ppg))
        out["decisions"] = {
            "window_open": window_open,
            "deadline": execution.get("owner_deadline") or home.rollover_deadline(today).isoformat(),
            "players": players,
        }
    if phase == "draft_prep":
        out["picks"] = home.upcoming_picks(teams.picks_view(state, ctx["team_id"])["owned"])
        counts: dict[str, int] = {}
        for r in state.get("roster", []):
            counts[str(r.get("league_team_id"))] = counts.get(str(r.get("league_team_id")), 0) + 1
        out["who_has_money"] = home.who_has_money(rows, season_index=0, roster_counts=counts)
        out["roster_count"] = counts.get(ctx["team_id"], 0)
        out["roster_max"] = home.ROSTER_MAX
        out["coming_off"] = home.coming_off_books(state.get("roster", []), ctx["team_id"])
    return out


class DecisionRequest(BaseModel):
    choice: str  # keep or walk


@app.post("/home/decisions/{decision_id}")
def rookie_decision(decision_id: str, body: DecisionRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Keep (extend) or let walk one rookie deal, through the same owner-only RPC as the website."""
    choice = {"keep": "recontract", "walk": "decline"}.get(body.choice)
    if choice is None:
        raise HTTPException(status_code=400, detail="Choose keep or walk.")
    client, user = owner
    team = resolve_team(client, user)
    try:
        OwnerRolloverDecisionService(client, team["league_id"]).submit(decision_id, choice, "Submitted from the iOS app")
    except OwnerRolloverDecisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}
