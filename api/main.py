"""Legacy API: the server the iPhone app talks to.

The app only holds the public Supabase key. Anything that needs the Python
league logic (standings, matchups, the Front Office AI) or a secret key runs
here instead. Every request carries the owner's Supabase access token, and
reads run as that owner so row-level security applies just like the website.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from supabase import Client, create_client

from api import box_score as box_score_mod
from api import account, demo, usage_log
from league_ai import screenshots
from api import gm_moves
from api import activity, alerts, cache, commissioner, owner_settings, profiles, prospects, front_office, home, league, lineups, live_activity, market, market_week, nfl_live, own_league, player, playoffs, projections, teams, trade_feed, trade_offers, waivers
from season_engine import SeasonResolver
from services.season_rollover_owner_ui import OwnerRolloverDecisionError, OwnerRolloverDecisionService

app = FastAPI(title="Legacy API")


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} in environment.")
    return value


def owner_client(request: Request, authorization: str = Header(default="")) -> tuple[Client, Any]:
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

    if demo.blocked(user, request.method, request.url.path):
        raise HTTPException(status_code=403, detail=demo.MESSAGE)
    client.postgrest.auth(token)
    request.state.owner = (client, user)  # lets the activity log below record what the owner did
    return client, user


@app.middleware("http")
async def log_owner_actions(request: Request, call_next):
    """After a successful POST/DELETE, one 'action' row in our own activity table (never slows or breaks the reply)."""
    response = await call_next(request)
    owner = getattr(request.state, "owner", None)
    if owner and response.status_code < 400 and usage_log.action_name(request.method, request.url.path):
        client, user = owner
        if not demo.is_demo(user):
            import asyncio

            def save() -> None:
                try:
                    league_id = resolve_team(client, user).get("league_id")
                except Exception:
                    league_id = None
                usage_log.save_action(client, user_id=str(user.id), league_id=league_id, method=request.method, path=request.url.path)

            asyncio.get_running_loop().run_in_executor(None, save)
    return response


def _first(rows: list[dict] | None) -> dict | None:
    return (rows or [None])[0]


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "Legacy API", "status": "running"}


_SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
_SITE_FILES = {"privacy.html": "text/html", "support.html": "text/html", "index.html": "text/html", "style.css": "text/css"}


@app.get("/site/{name}")
def public_page(name: str) -> Response:
    """The public privacy policy and support pages Apple links to (site/ in the repo). No sign-in."""
    kind = _SITE_FILES.get(name)
    if not kind:
        raise HTTPException(status_code=404, detail="Not found.")
    with open(os.path.join(_SITE, name), encoding="utf-8") as f:
        return Response(f.read(), media_type=f"{kind}; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def resolve_team(client: Client, user: Any) -> dict[str, Any]:
    """The owner's league and team, resolved like services/app_context.resolve_my_team."""
    return cache.cached(("team", user.id), 600, lambda: _resolve_team(client, user))


def _resolve_team(client: Client, user: Any) -> dict[str, Any]:
    membership = _pick_membership(client, user)
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


def _my_leagues(client: Client, user: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str | None]:
    """(my memberships, league id -> league row, the league I picked in Settings or None)."""
    rows = client.table("league_memberships").select("*").eq("user_id", user.id).execute().data or []
    ids = sorted({str(m["league_id"]) for m in rows if m.get("league_id")})
    try:
        leagues = {str(l["id"]): l for l in (client.table("leagues").select("*").in_("id", ids).execute().data or [])} if ids else {}
    except Exception:
        leagues = {}
    try:
        chosen = _first(client.table("user_active_league").select("league_id").eq("user_id", user.id).limit(1).execute().data)
    except Exception:  # before the test-league SQL is run
        chosen = None
    return rows, leagues, str(chosen["league_id"]) if chosen else None


def _pick_membership(client: Client, user: Any) -> dict[str, Any] | None:
    """The league picked in Settings; otherwise the first real (non-test) league, so a test copy never opens by itself."""
    rows, leagues, chosen = _my_leagues(client, user)
    picked = [m for m in rows if str(m.get("league_id")) == chosen]
    real = [m for m in rows if not (leagues.get(str(m.get("league_id"))) or {}).get("is_test")]
    return (picked or real or rows or [None])[0]


def _with_team_names(owner: tuple[Client, Any], result: Any) -> Any:
    """Standings, Matchups, a game and Home name teams by their team name (or short name where space is tight)."""
    client, user = owner
    try:
        profiles.label_teams(result, profiles.team_labels(client, resolve_team(client, user)["league_id"]))
    except Exception as exc:
        print(f"APP_TEAM_NAMES_ERROR {type(exc).__name__}", flush=True)
    return result


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
    _sync_own_league(client, team["league_id"], sleeper_id)
    return sleeper_id, _roster_names(client, team["league_id"], sleeper_id), team["sleeper_roster_id"]


def _sync_own_league(client: Client, league_id: str, sleeper_id: str) -> None:
    """Refresh the app's own scores (live watcher overlay, or the weeks the app runs itself) before any page reads
    matchups. Shared and cached 15 s in api.own_league, so pages like Matchups and Standings never show a frozen copy."""
    projections.remember_league(client, league_id, sleeper_id)  # projections and ranks score with our saved rules
    try:
        season = cache.cached(("season", league_id), 600, lambda: SeasonResolver(client).get_active_season(league_id))
        own_league.sync(client, league_id, sleeper_id, int(season.season))
    except Exception:
        pass


def _team_ids(client: Client, user: Any) -> dict[int, str]:
    """roster id -> league team id, so the app can open a team page from any row."""
    team = resolve_team(client, user)
    return {t["roster_id"]: t["team_id"] for t in _league_team_rows(client, team["league_id"])}


class PushToken(BaseModel):
    token: str
    platform: str | None = None
    tz: str | None = None


def _push_token_kind(token: str) -> str | None:
    """'apns' for an Apple device token (hex), None for anything else (Expo tokens are no longer used)."""
    return "apns" if 32 <= len(token) <= 400 and all(c in "0123456789abcdef" for c in token) else None


@app.delete("/me")
def delete_me(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Delete my account: personal data and sign-in go, league history stays (Apple requires this in the app)."""
    client, _ = owner
    try:
        result = account.delete_account(client)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return result


class AnswerReport(BaseModel):
    reason: str
    question: str | None = None
    answer: str | None = None
    conversation_id: str | None = None
    note: str | None = None


@app.post("/front-office/report")
def report_gm_answer(body: AnswerReport, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """An owner flags a GM answer as wrong, outdated or harmful. Read by the weekly AI report."""
    client, user = owner
    try:
        league_id = resolve_team(client, user).get("league_id")
    except HTTPException:
        league_id = None
    try:
        account.report_answer(client, user_id=str(user.id), league_id=league_id, reason=body.reason, question=body.question,
                              answer=body.answer, conversation_id=body.conversation_id, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True}


class CrashReport(BaseModel):
    message: str | None = None
    stack: str | None = None
    screen: str | None = None
    build: str | None = None
    version: str | None = None
    os: str | None = None


@app.post("/me/crash")
def crash_report(body: CrashReport, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """A crash or screen error from someone's phone, written to the server log (free, no extra service)."""
    client, user = owner
    print(account.crash_line(str(user.id), body.model_dump()), flush=True)
    if not demo.is_demo(user):
        try:
            league_id = resolve_team(client, user).get("league_id")
        except HTTPException:
            league_id = None
        usage_log.save_error(client, user_id=str(user.id), league_id=league_id, screen=body.screen, build=body.build)
    return {"ok": True}


class ScreenTime(BaseModel):
    screen: str
    seconds: int
    at: str | None = None
    kind: str | None = None  # screen (default), tap, swipe, push_open, app_open, app_close
    detail: dict[str, Any] | None = None


class ActivityBatch(BaseModel):
    views: list[ScreenTime]
    build: str | None = None


@app.post("/me/activity")
def save_activity(body: ActivityBatch, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Which screens owners open and for how long, stored in our own database (no outside analytics)."""
    client, user = owner
    if demo.is_demo(user):
        return {"ok": True, "saved": 0}
    try:
        league_id = resolve_team(client, user).get("league_id")
    except HTTPException:
        league_id = None
    saved = usage_log.save(client, user_id=str(user.id), league_id=league_id, views=[v.model_dump() for v in body.views], build=body.build, source="app")
    return {"ok": True, "saved": saved}


@app.post("/me/push-token")
def save_push_token(body: PushToken, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """This phone's Apple push token (and time zone, for quiet hours), so league alerts reach it."""
    client, user = owner
    token = body.token.strip().lower()
    kind = _push_token_kind(token)
    if not kind:
        raise HTTPException(status_code=400, detail="Not a push token.")
    league_id = resolve_team(client, user)["league_id"]
    tz = (body.tz or "").strip()[:64] or None
    try:
        client.table("push_tokens").upsert({"token": token, "user_id": user.id, "league_id": league_id, "platform": body.platform,
                                            "kind": kind, "tz": tz,
                                            "updated_at": datetime.now().astimezone().isoformat()}, on_conflict="token").execute()
    except Exception as exc:
        print(f"APP_PUSH_TOKEN_ERROR {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=503, detail="Alerts aren't switched on yet.")
    return {"ok": True}


class AlertSettings(BaseModel):
    off: list[str] = []
    tz: str | None = None


def _alert_pref(client: Client, user: Any) -> tuple[str, dict[str, Any] | None]:
    league_id = resolve_team(client, user)["league_id"]
    rows = (client.table("push_prefs").select("off,tz").eq("user_id", user.id).eq("league_id", league_id).limit(1)
            .execute().data or [])
    return league_id, (rows[0] if rows else None)


class ProfileBody(BaseModel):
    display_name: str | None = None
    team_name: str | None = None
    team_abbr: str | None = None
    picture_url: str | None = None
    keep_picture: bool = True


class GmPrefsBody(BaseModel):
    answer_length: str | None = None
    team_plan: str | None = None


class InviteBody(BaseModel):
    email: str


def _settings_team(client: Client, user: Any) -> dict[str, Any]:
    team = resolve_team(client, user)
    if not team.get("team_id"):
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    return team


@app.get("/me/settings")
def my_settings(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Everything on the Settings page: my profile, Front Office preferences, co-owner and who I am."""
    client, user = owner
    team = _settings_team(client, user)
    jobs = {
        "profile": cache.pool.submit(owner_settings.profile_view, client, team),
        "gm": cache.pool.submit(owner_settings.gm_prefs, client, user.id),
        "people": cache.pool.submit(owner_settings.people, client, team),
    }
    role = str(team.get("role") or "owner").lower()
    return {
        "email": getattr(user, "email", None), "role": role, "is_commissioner": role == "commissioner", "team_id": team["team_id"],
        "is_co_owner": role in ("co_owner", "co-owner"),
        "profile": jobs["profile"].result(), "gm": jobs["gm"].result(), "people": jobs["people"].result(),
        "leagues": _league_choices(client, user, team),
    }


def _league_choices(client: Client, user: Any, team: dict[str, Any]) -> list[dict[str, Any]]:
    """Settings > League: each league I'm in (only shown when there's more than one), and in a test league where I'm
    commissioner, every team so I can play each side of a trade."""
    try:
        rows, leagues, _ = _my_leagues(client, user)
    except Exception:
        return []
    out = []
    for m in rows:
        lid = str(m.get("league_id"))
        row = leagues.get(lid) or {}
        try:  # a test copy's seasons carry the mirror Sleeper id
            is_test = bool(row.get("is_test")) or league.sleeper.is_mirror(league.sleeper_league_id(client, lid))
        except Exception:
            is_test = bool(row.get("is_test"))
        item = {"league_id": lid, "name": "Legacy Test" if is_test else (row.get("name") or "Legacy Dynasty"),
                "is_test": is_test, "active": lid == str(team.get("league_id")), "teams": []}
        if item["is_test"] and item["active"] and str(m.get("role") or "").lower() == "commissioner":
            teams_rows = client.table("league_teams").select("id,team_name,owner_name,sleeper_roster_id").eq("league_id", lid).execute().data or []
            item["teams"] = sorted(({"team_id": t["id"], "name": t.get("team_name") or t.get("owner_name") or "Team",
                                     "mine": str(t["id"]) == str(team.get("team_id"))} for t in teams_rows),
                                   key=lambda t: str(t["name"]))
        out.append(item)
    return out if len(out) > 1 else []


class LeagueChoice(BaseModel):
    league_id: str
    team_id: str | None = None


@app.post("/me/league")
def choose_league(body: LeagueChoice, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Open another league I'm in (the test copy and back), or in a test league, play as another team."""
    client, user = owner
    try:
        client.rpc("set_active_league_authenticated", {"p_league_id": body.league_id}).execute()
        if body.team_id:
            client.rpc("switch_test_team_authenticated", {"p_league_id": body.league_id, "p_team_id": body.team_id}).execute()
    except Exception:
        raise HTTPException(status_code=400, detail="That league isn't available.")
    cache.clear()  # the team lookup and every league read are cached
    return resolve_team(client, user)


@app.post("/me/profile")
def save_my_profile(body: ProfileBody, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    team = _settings_team(client, user)
    data = body.model_dump()
    if data.pop("keep_picture"):
        data.pop("picture_url", None)
    try:
        return owner_settings.save_profile(client, team, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/me/gm-prefs")
def save_my_gm_prefs(body: GmPrefsBody, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    try:
        return owner_settings.save_gm_prefs(client, user.id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class StarBody(BaseModel):
    athlete_id: str
    on: bool = True


@app.get("/prospects/rankings")
def prospect_rankings(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    return prospects.page(client, str(user.id))


@app.post("/prospects/star")
def star_prospect(body: StarBody, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    try:
        return {"starred": prospects.set_star(client, str(user.id), body.athlete_id, body.on)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/me/co-owner")
def invite_my_co_owner(body: InviteBody, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    try:
        return owner_settings.invite_co_owner(client, user.id, _settings_team(client, user), body.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/me/co-owner/invite/{invite_id}")
def cancel_my_co_owner_invite(invite_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    try:
        owner_settings.cancel_invite(client, _settings_team(client, user), invite_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.delete("/me/co-owner/{member_user_id}")
def remove_my_co_owner(member_user_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, _ = owner
    try:
        owner_settings.remove_co_owner(client, member_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.get("/league/rules")
def league_rules(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    try:
        return owner_settings.rules_view(client, resolve_team(client, user)["league_id"])
    except Exception as exc:
        print(f"APP_RULES_ERROR {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=502, detail="Couldn't load the league rules. Try again.")


@app.get("/me/alerts")
def my_alerts(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Which alerts I get (each kind on or off), quiet hours and the weekly limit. Same switches as the website."""
    client, user = owner
    try:
        _, pref = _alert_pref(client, user)
    except HTTPException:
        raise
    except Exception as exc:
        print(f"APP_ALERTS_ERROR {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=503, detail="Alerts aren't switched on yet.")
    return alerts.settings_view(pref)


@app.post("/me/alerts")
def save_my_alerts(body: AlertSettings, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    try:
        league_id, pref = _alert_pref(client, user)
        row = {"user_id": user.id, "league_id": league_id, "off": alerts.clean_off(body.off),
               "tz": ((body.tz or "").strip()[:64] or (pref or {}).get("tz")),
               "updated_at": datetime.now(timezone.utc).isoformat()}
        client.table("push_prefs").upsert(row, on_conflict="user_id,league_id").execute()
    except HTTPException:
        raise
    except Exception as exc:
        print(f"APP_ALERTS_ERROR {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=503, detail="Alerts aren't switched on yet.")
    return alerts.settings_view(row)


@app.post("/me/alerts/test")
def test_my_alerts(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Queue a test alert for my phones; the alert job sends it within about 10 minutes (quiet hours don't apply)."""
    client, user = owner
    league_id = resolve_team(client, user)["league_id"]
    now = datetime.now(timezone.utc)
    test = alerts.test_alert(now)
    try:
        alerts.enqueue(client, alerts.Alert(test.kind, test.key, test.title, test.body, test.url, urgent=True),
                       league_id=league_id, user_ids=[user.id], expires_at=now + timedelta(hours=1))
    except Exception as exc:
        print(f"APP_ALERTS_ERROR {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=503, detail="Alerts aren't switched on yet.")
    return {"ok": True, "message": "A test alert is on its way. It arrives within about 10 minutes."}


class LiveActivityToken(BaseModel):
    activity_id: str
    push_token: str
    week: int
    matchup_id: int


def _my_live_game(client: Client, user: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """This week's box score of my game (None when I have no game this week)."""
    ctx = _season_context(client, user)
    week = league.current_week()
    current = league.matchups(ctx["sleeper_id"], ctx["names"], week, ctx["sleeper_roster_id"])
    mine = home.my_game(current["games"], ctx["sleeper_roster_id"])
    if not mine or not mine.get("matchup_id"):
        return ctx, None
    return ctx, box_score_mod.game(client, ctx, week, int(mine["matchup_id"]), _league_team_rows(client, ctx["league_id"]))


@app.get("/live-activity")
def live_activity_now(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """What the lock screen Live Activity starts with, and whether my matchup is live right now."""
    client, user = owner
    ctx, result = _my_live_game(client, user)
    if not result:
        return {"live": False}
    props = live_activity.props(result, ctx["sleeper_roster_id"])
    return {"live": live_activity.is_live(result) and props is not None, "week": result["week"], "matchup_id": result["matchup_id"],
            "url": live_activity.deep_link(result["week"], result["matchup_id"]), "props": props}


@app.post("/me/live-activity")
def save_live_activity(body: LiveActivityToken, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """The running Live Activity's push token, so the game-day watcher can send it score updates."""
    client, user = owner
    token = body.push_token.strip().lower()
    if not token or len(token) > 400 or any(c not in "0123456789abcdef" for c in token):
        raise HTTPException(status_code=400, detail="Not a Live Activity push token.")
    team = resolve_team(client, user)
    if team.get("sleeper_roster_id") is None:
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    now = datetime.now(timezone.utc).isoformat()
    try:
        client.table("live_activities").upsert({
            "activity_id": body.activity_id[:200], "user_id": user.id, "league_id": team["league_id"],
            "roster_id": int(team["sleeper_roster_id"]), "week": body.week, "matchup_id": body.matchup_id,
            "push_token": token, "updated_at": now, "ended_at": None,
        }, on_conflict="activity_id").execute()
    except Exception as exc:
        print(f"APP_LIVE_ACTIVITY_ERROR {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=503, detail="Lock screen scores aren't switched on yet.")
    return {"ok": True}


@app.post("/me/live-activity/{activity_id}/end")
def end_live_activity(activity_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """The phone ended or dismissed it: stop sending updates."""
    client, user = owner
    try:
        client.table("live_activities").update({"ended_at": datetime.now(timezone.utc).isoformat()}).eq("activity_id", activity_id).eq("user_id", user.id).execute()
    except Exception as exc:
        print(f"APP_LIVE_ACTIVITY_ERROR {type(exc).__name__}", flush=True)
    return {"ok": True}


@app.get("/standings")
def standings(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _with_team_names(owner, _standings_raw(owner))


def _standings_raw(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    sleeper_id, names, my_roster_id = _league_context(*owner)
    result = league.standings(sleeper_id, names, my_roster_id)
    ids = _team_ids(*owner)
    for row in result["rows"]:
        row["team_id"] = ids.get(row["roster_id"])
    return result


@app.get("/matchups")
def matchups(week: int | None = Query(default=None, ge=1, le=25), owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _with_team_names(owner, _matchups_raw(week, owner))


def _matchups_raw(
    week: int | None = Query(default=None, ge=1, le=25),
    owner: tuple[Client, Any] = Depends(owner_client),
) -> dict[str, Any]:
    sleeper_id, names, my_roster_id = _league_context(*owner)
    league_id = resolve_team(*owner)["league_id"]
    try:
        rules = cache.cached(("playoff_rules", league_id), 300, lambda: league.playoff_rules(owner[0], league_id))
    except Exception:
        rules = playoffs.rules_of({})
    last_week = playoffs.last_week(rules)
    shown = min(week or league.current_week(), last_week)
    result = league.matchups(sleeper_id, names, shown, my_roster_id)
    result["current_week"] = min(league.current_week(), last_week)
    result["last_week"] = last_week
    try:  # playoff weeks: our bracket, never Sleeper's made-up regular-season pairings
        pics = _pictures(owner[0], league_id, sleeper_id)
        view = league.playoff_view(sleeper_id, names, shown, my_roster_id, rules, pics)
    except Exception as exc:
        print(f"APP_PLAYOFFS_ERROR {type(exc).__name__}", flush=True)
        view = None
    if view is not None:
        if shown <= result["current_week"]:
            try:  # every playoff and consolation game shows projected scores (Tommy, 2026-11-22)
                playoffs.add_projections(view, _week_projections(*owner, shown), week_final=bool(result.get("is_final")))
            except Exception as exc:
                print(f"APP_PLAYOFF_PROJ_ERROR {type(exc).__name__}", flush=True)
        result["playoffs"] = view
        result["games"] = playoffs.legacy_games(view)
        result["summary"] = None
        return result
    try:
        _add_card_owners(owner[0], league_id, sleeper_id, names, my_roster_id, result)
    except Exception as exc:
        print(f"APP_MATCHUP_OWNERS_ERROR {type(exc).__name__}", flush=True)
    if not result["is_final"]:
        try:
            _add_card_projections(*owner, result)
        except Exception as exc:
            print(f"APP_MATCHUP_PROJ_ERROR {type(exc).__name__}: {exc}", flush=True)
    return result


def _pictures(client: Client, league_id: str, sleeper_id: str) -> dict[int, str | None]:
    """roster id -> owner picture: the owner's own from Settings, else their Sleeper one."""
    sleeper_pics = home.avatars(league.sleeper.get(f"league/{sleeper_id}/users") or [], league.sleeper.get(f"league/{sleeper_id}/rosters") or [])
    return profiles.pictures(profiles.by_roster(client, league_id), sleeper_pics)


def _add_card_owners(client: Client, league_id: str, sleeper_id: str, names: dict[int, str], my_roster_id: int | None,
                     result: dict[str, Any]) -> None:
    """Each card row's owner picture and season record."""
    pics = _pictures(client, league_id, sleeper_id)
    rows = cache.cached(("standings", sleeper_id), 300, lambda: league.standings(sleeper_id, names, my_roster_id))["rows"]
    records = {r["roster_id"]: r for r in rows}
    for game in result["games"]:
        for team in game["teams"]:
            team["avatar"] = pics.get(team["roster_id"])
            rec = records.get(team["roster_id"])
            if rec:
                team["record"] = f"{rec['wins']}-{rec['losses']}" + (f"-{rec['ties']}" if rec["ties"] else "")


CARD_PROJ_TTL = 15


def _week_projections(client: Client, user: Any, week: int) -> dict[int, dict[str, float]]:
    """roster_id -> {"projected": live projected week total, "kickoff": starters' projections frozen at kickoff},
    league-wide and shared by every owner (one scoring pass per refresh window)."""
    ctx = _season_context(client, user)

    def load() -> dict[int, dict[str, float]]:
        rows = league.sleeper.get(f"league/{ctx['sleeper_id']}/matchups/{week}", ttl=league.LIVE_TTL) or []
        sides = []
        for row in rows:
            points = row.get("players_points") or {}
            starters = [str(pid) for pid in row.get("starters") or [] if pid and str(pid) != "0"]
            sides.append({"roster_id": row.get("roster_id"), "players": [{"player_id": pid, "points": float(points.get(pid) or 0)} for pid in starters]})
        info = teams.player_info(client, [p["player_id"] for side in sides for p in side["players"]])
        box_score_mod.add_projections(client, ctx, week, False, {"teams": sides}, info)
        return {int(side["roster_id"]): {"projected": side.get("projected"),
                                          "kickoff": round(sum(p.get("proj") or 0.0 for p in side["players"]), 1)}
                for side in sides if side.get("roster_id") is not None}

    return cache.cached(("week_proj", ctx["sleeper_id"], ctx["season"], week), CARD_PROJ_TTL, load)


def _add_card_projections(client: Client, user: Any, result: dict[str, Any]) -> None:
    """Each side's projected total on the week's game cards, scored the same way as the box score."""
    ctx = _season_context(client, user)
    week = result["week"]

    def load() -> list[dict[str, Any]]:
        rows = league.sleeper.get(f"league/{ctx['sleeper_id']}/matchups/{week}", ttl=league.LIVE_TTL) or []
        sides = []
        for row in rows:
            points = row.get("players_points") or {}
            starters = [str(pid) for pid in row.get("starters") or [] if pid and str(pid) != "0"]
            sides.append({"roster_id": row.get("roster_id"), "players": [{"player_id": pid, "points": float(points.get(pid) or 0)} for pid in starters]})
        info = teams.player_info(client, [p["player_id"] for side in sides for p in side["players"]])
        box_score_mod.add_projections(client, ctx, week, False, {"teams": sides}, info)
        return sides

    # League-wide and the same for every owner: one scoring pass per live refresh window, shared by all.
    sides = cache.cached(("card_proj", ctx["sleeper_id"], ctx["season"], week), CARD_PROJ_TTL, load)
    by_roster = {side["roster_id"]: side for side in sides}
    for game in result["games"]:
        pair = [by_roster.get(team["roster_id"]) for team in game["teams"]]
        kicks = []
        for team, side in zip(game["teams"], pair):
            if not side:
                continue
            team["projected"] = side.get("projected")
            statuses = [p.get("status") for p in side["players"]]
            team["playing"], team["to_play"] = statuses.count("playing"), statuses.count("waiting")
            kicks += [p["kickoff"] for p in side["players"] if p.get("status") == "waiting" and p.get("kickoff")]
        if all(pair):
            game["win_chance"] = nfl_live.win_chance([{**side, "score": team.get("score")} for team, side in zip(game["teams"], pair)], False)
        game["next_kickoff"] = min(kicks) if kicks else None


def _season_context(client: Client, user: Any) -> dict[str, Any]:
    """Everything the team pages share: who I am, the season, and Sleeper ids."""
    team = resolve_team(client, user)
    season = cache.cached(("season", team["league_id"]), 600, lambda: SeasonResolver(client).get_active_season(team["league_id"]))
    if not season.sleeper_league_id:
        raise HTTPException(status_code=409, detail="The active season isn't linked to a Sleeper league yet.")
    sleeper_id = season.sleeper_league_id
    _sync_own_league(client, team["league_id"], sleeper_id)  # our scores replace Sleeper's for every page below
    _process_due_waivers(client, team["league_id"])
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
    """Contracts for this season and the next three, shared by the league for a minute."""
    return cache.cached(("state", league_id, season), 60, lambda: teams.seasons_state(client, league_id, season))


def _rookie_deals(client: Client, league_id: str) -> dict[str, dict[str, Any]] | None:
    """Rookie deals from every one of our rookie drafts (end year with taxi seasons, option price); None if unreadable."""
    try:
        return cache.cached(("rookie_deals", league_id), 600, lambda: teams.rookie_deals(client, league_id))
    except Exception as exc:
        print(f"APP_ROOKIE_DRAFTS_ERROR {type(exc).__name__}", flush=True)
        return None


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
    mine = profiles.by_team(client, ctx["league_id"])
    rows = [profiles.overlay_team(dict(r), mine.get(r["team_id"])) for r in rows]
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


def _team_page(client: Client, ctx: dict[str, Any], team_id: str, week: int | None = None) -> dict[str, Any]:
    rows, states, standings_rows, cap = _league_teams(client, ctx)
    summary = next((t for t in rows if t["team_id"] == team_id), None)
    if summary is None:
        raise HTTPException(status_code=404, detail="Team not found in your league.")
    summary = profiles.overlay_team(dict(summary), profiles.by_team(client, ctx["league_id"]).get(team_id))

    state = states[0]
    weeks = league.last_scored_week(ctx["sleeper_id"])
    ids = [r.get("sleeper_player_id") for r in state.get("roster", []) if str(r.get("league_team_id")) == team_id]
    try:  # plus anyone on his Sleeper roster without a contract row yet
        ids += [pid for pid, rid in league.sleeper_owners(ctx["sleeper_id"]).items() if rid == summary["roster_id"]]
    except Exception:
        pass
    standing = next((r for r in standings_rows if r["roster_id"] == summary["roster_id"]), None)
    _add_contract_lengths(client, state, team_id)
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
        app_lineup=_app_lineup(client, ctx, team_id, week) if _transactions_on(client, ctx["league_id"]) else None,
        rookie_deals=_rookie_deals(client, ctx["league_id"]),
    )
    _add_pick_slots(detail["picks"], rows, standings_rows, ctx["season"])
    try:
        this_week = _team_week(client, ctx, summary["roster_id"], week)
    except Exception:
        this_week = None
    current = _home_week(client, ctx["season"], datetime.now(home.HOME_TZ).date())
    if week and this_week and this_week.get("is_final") and week < current and this_week.get("starter_ids"):
        # A finished week from the week menu shows the lineup actually started that week, with that week's points;
        # there's nothing to edit in the past.
        past_ids = [p for p in this_week["starter_ids"] + this_week.get("bench_ids", []) if p and p != "0"]
        detail["roster"] = teams.week_lineup(detail["roster"], this_week["starter_ids"], this_week.get("bench_ids", []),
                                             ctx["slots"], teams.player_info(client, past_ids), teams.player_ppg(ctx["sleeper_id"], weeks))
        detail["lineup"] = None
    try:
        weeks = _team_weeks(client, ctx, summary["roster_id"])
    except Exception as exc:
        print(f"APP_TEAM_WEEKS_ERROR {type(exc).__name__}", flush=True)
        weeks = None
    return {"season": ctx["season"], "salary_cap": cap, "sleeper_league_id": ctx["sleeper_id"], **detail, "week": this_week, "weeks": weeks,
            "current_week": current}


def _add_contract_lengths(client: Client, state: dict[str, Any], team_id: str) -> None:
    """Each of this team's contracts gets its full length (every season on the deal), for the Contracts tab dots
    (filled = years left, all dots = whole contract). Leaves rows alone when the read fails."""
    ids = [str(r["contract_agreement_id"]) for r in state.get("roster", [])
           if str(r.get("league_team_id")) == team_id and r.get("contract_agreement_id")]
    if not ids:
        return
    def load() -> dict[str, int]:
        rows = client.table("contract_seasons").select("contract_id,season").in_("contract_id", ids).execute().data or []
        out: dict[str, int] = {}
        for r in rows:
            out[str(r["contract_id"])] = out.get(str(r["contract_id"]), 0) + 1
        return out
    try:
        lengths = cache.cached(("contract_lengths", team_id, tuple(sorted(ids))), 600, load)
    except Exception as exc:
        print(f"APP_CONTRACT_LENGTHS_ERROR {type(exc).__name__}", flush=True)
        return
    for r in state.get("roster", []):
        n = lengths.get(str(r.get("contract_agreement_id")))
        if n:
            r["contract_total_years"] = n


def _add_pick_slots(picks: dict[str, Any], rows: list[dict[str, Any]], standings_rows: list[dict[str, Any]], season: int) -> None:
    """Next year's picks get a projected slot (1.06): the draft runs straight reverse of the standings."""
    rank_of = {r["roster_id"]: r["rank"] for r in standings_rows}
    owner_rank = {str(t.get("owner_name")): rank_of.get(t.get("roster_id")) for t in rows}
    count = len(standings_rows)
    for group in ("owned", "traded_away"):
        for p in picks.get(group, []):
            rank = owner_rank.get(p.get("original_owner"))
            if p.get("year") == season + 1 and rank and count:
                p["slot"] = f"{p['round']}.{count - rank + 1:02d}"


def _team_weeks(client: Client, ctx: dict[str, Any], roster_id: int | None) -> list[dict[str, Any]] | None:
    """Every week of the season for the My Team week menu: finished weeks with the result, later weeks with the
    opponent, then the playoff weeks."""
    if roster_id is None:
        return None
    sid = ctx["sleeper_id"]

    def load() -> dict[str, Any]:
        scored, end = league.last_scored_week(sid), league.regular_season_end(sid)
        try:
            rules = league.playoff_rules(client, ctx["league_id"])
        except Exception:
            rules = playoffs.rules_of({})
        out = []
        for wk in range(1, min(end, 18) + 1):
            done = wk <= scored
            rows = league.sleeper.get(f"league/{sid}/matchups/{wk}", ttl=86400 if done else 3600) or []
            mine = next((r for r in rows if r.get("roster_id") == roster_id), None)
            opp = next((r for r in rows if mine and r is not mine and r.get("matchup_id") and r.get("matchup_id") == mine.get("matchup_id")), None)
            item: dict[str, Any] = {"week": wk, "opponent": ctx["names"].get(opp.get("roster_id")) if opp else None}
            if done and mine and opp:
                me_pts, them_pts = float(mine.get("points") or 0), float(opp.get("points") or 0)
                item["result"] = "W" if me_pts > them_pts else "L" if me_pts < them_pts else "T"
                item["score"] = [round(me_pts, 1), round(them_pts, 1)]
            out.append(item)
        for wk in range(int(rules["playoff_week_start"]), playoffs.last_week(rules) + 1):
            out.append({"week": wk, "playoffs": True})
        return {"weeks": out}

    return cache.cached(("team_weeks", sid, ctx["season"], roster_id), 600, load)["weeks"]


TEAM_WEEK_TTL = 15


def _team_week(client: Client, ctx: dict[str, Any], roster_id: int | None, pick: int | None = None) -> dict[str, Any] | None:
    """This week for one team's roster page: each player's projection, live points and live projection, game and
    stat line (scored the same way as the box score), the team's total and projected total, and its odds against
    this week's opponent. None when the team has no game this week."""
    if roster_id is None:
        return None
    # Same week as Home (Monday's holds until midnight Pacific), unless the week menu picked another one.
    week = pick or _home_week(client, ctx["season"], datetime.now(home.HOME_TZ).date())
    final = week <= league.last_scored_week(ctx["sleeper_id"])

    def load() -> dict[str, Any] | None:
        rows = league.sleeper.get(f"league/{ctx['sleeper_id']}/matchups/{week}", ttl=86400 if final else league.LIVE_TTL) or []
        mine = next((r for r in rows if r.get("roster_id") == roster_id), None)
        if not mine:
            return None
        opp = next((r for r in rows if r is not mine and r.get("matchup_id") and r.get("matchup_id") == mine.get("matchup_id")), None)

        def side(row: dict[str, Any], with_bench: bool) -> dict[str, Any]:
            pts = row.get("players_points") or {}
            starters = [str(p) for p in row.get("starters") or [] if p and str(p) != "0"]
            out = {"roster_id": row.get("roster_id"), "players": [{"player_id": p, "points": float(pts.get(p) or 0)} for p in starters],
                   "score": round(float(row.get("points") or sum(float(pts.get(p) or 0) for p in starters)), 2)}
            if with_bench:
                out["bench"] = [{"player_id": str(p), "points": float(pts.get(str(p)) or 0)} for p in row.get("players") or [] if p and str(p) not in starters]
            return out

        sides = [side(mine, True)] + ([side(opp, False)] if opp else [])
        info = teams.player_info(client, [p["player_id"] for s in sides for p in s["players"] + s.get("bench", [])])
        box_score_mod.add_projections(client, ctx, week, final, {"teams": sides}, info)
        me = sides[0]
        keys = ("points", "proj", "live_proj", "status", "game", "line", "kickoff")
        odds = nfl_live.win_chance(sides, final) if opp else None
        # The lineup exactly as it was set that week (empty slots kept), for the week menu's past weeks.
        starter_ids = [str(p or "") for p in mine.get("starters") or []]
        try:
            bench_ids = teams.bench_ids(ctx["sleeper_id"], mine, starter_ids)
        except Exception:
            bench_ids = [p["player_id"] for p in me.get("bench", [])]
        return {
            "week": week, "is_final": final, "score": me["score"], "projected": me.get("projected"),
            "starter_ids": starter_ids, "bench_ids": bench_ids,
            "opponent": {"name": ctx["names"].get(opp.get("roster_id"), "Opponent"), "score": sides[1]["score"],
                         "projected": sides[1].get("projected")} if opp else None,
            "win_chance": odds[0] if odds else None,
            "players": {p["player_id"]: {k: p.get(k) for k in keys} for p in me["players"] + me.get("bench", [])},
        }

    return cache.cached(("team_week", ctx["sleeper_id"], ctx["season"], week, roster_id), TEAM_WEEK_TTL, load)


def _lineup_slots(client: Client, league_id: str) -> list[str]:
    """The league's starting slots as saved in Settings (the same list the database checks lineups against)."""
    def load() -> dict[str, Any]:
        from league_ai.settings_store import load_settings
        return {"slots": lineups.starter_slots(load_settings(client, league_id).roster_positions)}
    return cache.cached(("lineup_slots", league_id), 600, load)["slots"]


def _week_kickoffs(client: Client, season: int, week: int) -> dict[str, Any]:
    def load() -> dict[str, Any]:
        rows = (client.table("nfl_games").select("gameday,gametime,home_team,away_team")
                .eq("season", season).eq("week", week).execute().data or [])
        return {"rows": rows}
    return lineups.kickoffs(cache.cached(("kickoffs", season, week), 600, load)["rows"])


def _app_lineup(client: Client, ctx: dict[str, Any], team_id: str, pick: int | None = None):
    """This week's app lineup for one team, for team_detail: (slot label, player id) pairs plus the editor's view."""
    def build(active: set[str], sleeper_starters: list[str]) -> tuple[list[tuple[str, str | None]], dict[str, Any]]:
        # The week menu can open a later week to plan it; the saved lineup carries forward until changed.
        week, slots = pick or league.current_week(), _lineup_slots(client, ctx["league_id"])
        saved = (client.table("league_lineups").select("week,slots").eq("league_id", ctx["league_id"])
                 .eq("league_team_id", team_id).eq("season", ctx["season"]).lte("week", week)
                 .order("week", desc=True).limit(1).execute().data or [])
        ids = lineups.effective(slots, saved, sleeper_starters, active)
        starts, now = _week_kickoffs(client, ctx["season"], week), datetime.now(timezone.utc)
        info = teams.player_info(client, active)
        locked = {pid for pid in active if lineups.is_locked(pid, info.get(pid, {}).get("team"), starts, now)}
        mine = team_id == ctx["team_id"]
        commish = str(ctx.get("role") or "").lower() == "commissioner"  # sets an away owner's lineup, same locks
        view = lineups.view(slots, ids, week, locked, bool(slots) and (mine or commish))
        if not mine and commish:
            view["commissioner_team_id"] = team_id
        return [(lineups.label(s), ids[i]) for i, s in enumerate(slots)], view
    return build


class LineupRequest(BaseModel):
    week: int
    slots: list[str | None]


@app.post("/my-team/lineup")
def set_lineup(body: LineupRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Save this week's starters (player ids in slot order, None for an empty slot) through the checked database call."""
    client, user = owner
    ctx = _season_context(client, user)
    if not ctx["team_id"]:
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    slots = _lineup_slots(client, ctx["league_id"])
    if len(body.slots) != len(slots):
        raise HTTPException(status_code=400, detail=lineups.ERRORS["lineup_shape_invalid"])
    try:
        client.rpc("set_lineup_authenticated", {"p_request": {
            "league_id": ctx["league_id"], "season": ctx["season"], "week": body.week,
            "slots": [{"slot": s, "player_id": body.slots[i] or None} for i, s in enumerate(slots)],
        }}).execute()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=lineups.friendly(exc))
    return {"ok": True}


class CommishLineupRequest(BaseModel):
    team_id: str
    week: int
    slots: list[str | None]


def _commissioner_ctx(client: Client, user: Any) -> dict[str, Any]:
    ctx = _season_context(client, user)
    if str(ctx.get("role") or "").lower() != "commissioner":
        raise HTTPException(status_code=403, detail="Only the commissioner can do that.")
    return ctx


@app.post("/commissioner/lineup")
def commissioner_lineup(body: CommishLineupRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """The commissioner sets an away owner's lineup, under the same slot rules and kickoff locks."""
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    slots = _lineup_slots(client, ctx["league_id"])
    if len(body.slots) != len(slots):
        raise HTTPException(status_code=400, detail=lineups.ERRORS["lineup_shape_invalid"])
    try:
        commissioner.set_lineup(client, league_id=ctx["league_id"], team_id=body.team_id, season=ctx["season"], week=body.week,
                                slots=slots, player_ids=body.slots)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


class CommishAdd(BaseModel):
    team_id: str
    player_id: str
    salary: float = 1.0
    years: int = 1
    notes: str = ""


class CommishDrop(BaseModel):
    player_id: str
    notes: str = ""


class CommishContract(BaseModel):
    contract_id: str
    salary: float | None = None
    end_season: int | None = None
    notes: str = ""


class CommishInvite(BaseModel):
    team_id: str
    email: str
    role: str = "owner"


class CommishSwitch(BaseModel):
    enabled: bool
    own_from_week: int | None = None


class CommishRules(BaseModel):
    values: dict[str, Any] = {}
    scoring: list[dict[str, Any]] | None = None


def _commish_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _after_commish_change(league_id: str) -> None:
    """Rosters, contracts and names show the change on the next load."""
    for kind in ("state", "cap", "league_teams", "names", "profiles", "team_labels", "rules_view", "playoff_rules"):
        cache.forget(kind, league_id)


@app.get("/commissioner")
def commissioner_home(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    try:
        viewer = bool(client.table("app_activity_viewers").select("user_id").eq("user_id", str(user.id)).limit(1).execute().data)
    except Exception:
        viewer = False
    return {"season": ctx["season"], "activity": viewer, **_commish_call(commissioner.overview, client, ctx["league_id"])}


def _activity_viewer(client: Client, user: Any) -> dict[str, Any]:
    """Activity is only for the accounts in app_activity_viewers (the league owner), not every commissioner."""
    ctx = _commissioner_ctx(client, user)
    try:
        ok = bool(client.table("app_activity_viewers").select("user_id").eq("user_id", str(user.id)).limit(1).execute().data)
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Owner activity is private to the league owner's account.")
    return ctx


def _activity_data(client: Client, league_id: str, days: int) -> tuple[list[dict], list[dict], dict[str, dict]]:
    from api import usage_stats
    def load() -> tuple[list[dict], list[dict], dict[str, dict]]:
        rows, ai = usage_stats.load(client, league_id, days=days)
        owners = usage_stats.owners_from_teams(commissioner.overview(client, league_id)["teams"])
        return rows, ai, owners
    return cache.cached(("activity", league_id, days), 60, load)


@app.get("/commissioner/activity")
def commissioner_activity(days: int = Query(30, ge=1, le=365), by: str = "day", tz: str | None = None, owner: str | None = None,
                          owner_auth: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """How owners use the app and website: pages, flows, sessions, timelines, by day/week/month (commissioner only)."""
    from api import usage_stats
    client, user = owner_auth
    ctx = _activity_viewer(client, user)
    rows, ai, owners = _activity_data(client, ctx["league_id"], days)
    return usage_stats.build(rows, owners=owners, ai_rows=ai, days=days, tz=tz, owner=owner, by=by)


@app.get("/commissioner/activity/feed")
def commissioner_activity_feed(request: Request, days: int = Query(60, ge=1, le=365), tz: str | None = None,
                               owner_auth: tuple[Client, Any] = Depends(owner_client)) -> Response:
    """Every event of the range in the console's compact shape (api/usage_console.py), gzipped."""
    from api import usage_console
    client, user = owner_auth
    ctx = _activity_viewer(client, user)
    rows, ai, owners = _activity_data(client, ctx["league_id"], days)
    body, headers = usage_console.gz_json(usage_console.feed(rows, owners=owners, ai_rows=ai, days=days, tz=tz),
                                          request.headers.get("accept-encoding", ""))
    return Response(body, media_type="application/json", headers=headers)


@app.get("/commissioner/activity/session")
def commissioner_activity_session(key: str, days: int = Query(60, ge=1, le=365), tz: str | None = None, authorization: str = Header(default=""),
                                  owner_auth: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """One session's steps plus short-lived links to its replay pictures (signed as the viewer, so the bucket's rule applies)."""
    from api import usage_console
    client, user = owner_auth
    ctx = _activity_viewer(client, user)
    rows, _, owners = _activity_data(client, ctx["league_id"], days)
    s = usage_console.session_detail(rows, owners=owners, key=key, tz=tz)
    if not s:
        raise HTTPException(status_code=404, detail="That session isn't in this range.")
    try:
        urls = usage_console.sign_frames(authorization.partition(" ")[2], [f["path"] for f in s["frames"]])
    except Exception as exc:
        print(f"REPLAY_SIGN_FAILED {type(exc).__name__}", flush=True)
        urls = {}
    s.pop("user_id", None)
    s["frames"] = [{"at": f["at"], "url": urls.get(f["path"])} for f in s["frames"] if urls.get(f["path"])]
    return s


class ActivityReport(BaseModel):
    spec: dict[str, Any]


@app.get("/commissioner/activity/reports")
def activity_reports(owner_auth: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """The viewer's saved console reports (activity_reports: each account reads and writes only its own)."""
    client, user = owner_auth
    _activity_viewer(client, user)
    rows = client.table("activity_reports").select("id,spec,created_at").eq("user_id", str(user.id)).order("created_at").execute().data or []
    return {"reports": [{"id": r["id"], **(r.get("spec") or {})} for r in rows]}


@app.post("/commissioner/activity/reports")
def save_activity_report(body: ActivityReport, owner_auth: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    from api import usage_console
    client, user = owner_auth
    _activity_viewer(client, user)
    spec = usage_console.clean_report(body.spec)
    mine = client.table("activity_reports").select("id,spec").eq("user_id", str(user.id)).execute().data or []
    same = next((r for r in mine if (r.get("spec") or {}).get("name") == spec["name"]), None)
    if same:
        client.table("activity_reports").update({"spec": spec}).eq("id", same["id"]).eq("user_id", str(user.id)).execute()
        return {"id": same["id"], **spec}
    if len(mine) >= 100:
        raise HTTPException(status_code=400, detail="That's 100 saved reports. Delete one first.")
    row = client.table("activity_reports").insert({"user_id": str(user.id), "spec": spec}).execute().data or [{}]
    return {"id": row[0].get("id"), **spec}


@app.delete("/commissioner/activity/reports/{report_id}")
def delete_activity_report(report_id: str, owner_auth: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner_auth
    _activity_viewer(client, user)
    client.table("activity_reports").delete().eq("id", report_id).eq("user_id", str(user.id)).execute()
    return {"ok": True}


class ActivityQuestion(BaseModel):
    question: str
    days: int = 30
    tz: str | None = None
    deep: bool = False
    history: list[dict[str, str]] = []


@app.post("/commissioner/activity/ask")
def commissioner_activity_ask(body: ActivityQuestion, owner_auth: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """The Activity analyst: Claude answers usage questions from our own activity rows, and can hand back a
    report for the console to draw and sessions for it to play."""
    from api import usage_ai
    from league_ai import usage
    client, user = owner_auth
    ctx = _activity_viewer(client, user)
    question = body.question.strip()[:1000]
    if not question:
        raise HTTPException(status_code=400, detail="Ask a question first.")
    days = max(1, min(int(body.days or 30), 365))
    rows, ai, owners = _activity_data(client, ctx["league_id"], days)
    try:
        out = usage_ai.ask(question, rows=rows, owners=owners, ai_rows=ai, days=days, tz=body.tz, deep=body.deep, history=body.history)
    except Exception as exc:
        print(f"USAGE_ANALYST_FAILED {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=502, detail="The analyst couldn't answer just now. Try again in a minute.")
    u = out["usage"]
    usage.record(client, usage.usage_row(league_id=ctx["league_id"], user_id=str(user.id), feature="usage_analyst", model=out["model"],
                                         input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                                         cache_read_tokens=u.get("cache_read_tokens", 0), cache_write_tokens=u.get("cache_write_tokens", 0),
                                         tool_calls=out["tools"], latency_ms=out["latency_ms"]))
    cost = usage.cost_usd(out["model"], input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                          cache_read_tokens=u.get("cache_read_tokens", 0), cache_write_tokens=u.get("cache_write_tokens", 0))
    return {"answer": out["answer"], "report": out["report"], "sessions": out["sessions"], "sessions_title": out["sessions_title"],
            "model": out["model"], "cost": cost}


_CONSOLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console", "console.html")


@app.get("/console")
def activity_console() -> Response:
    """The Owner activity console page (no data in it; it asks the endpoints above with the viewer's sign-in)."""
    with open(_CONSOLE, encoding="utf-8") as f:
        return Response(f.read(), media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "Content-Security-Policy": "frame-ancestors *"})


@app.post("/commissioner/add")
def commissioner_add(body: CommishAdd, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    if body.salary < 0 or not 1 <= body.years <= 10:
        raise HTTPException(status_code=400, detail="Check the salary and years.")
    _commish_call(commissioner.add_player, client, league_id=ctx["league_id"], season=ctx["season"], team_id=body.team_id,
                  player_id=body.player_id, salary=body.salary, years=body.years, notes=body.notes)
    _after_commish_change(ctx["league_id"])
    return {"ok": True}


@app.get("/commissioner/drop/{player_id}")
def commissioner_drop_preview(player_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    return _commish_call(commissioner.drop_preview, client, ctx["league_id"], player_id)


@app.post("/commissioner/drop")
def commissioner_drop(body: CommishDrop, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    _commish_call(commissioner.drop_player, client, ctx["league_id"], body.player_id, body.notes)
    _after_commish_change(ctx["league_id"])
    return {"ok": True}


@app.get("/commissioner/contract/{player_id}")
def commissioner_contract(player_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    return _commish_call(commissioner.contract_for, client, ctx["league_id"], player_id)


@app.post("/commissioner/contract")
def commissioner_fix_contract(body: CommishContract, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    _commish_call(commissioner.fix_contract, client, body.contract_id, body.salary, body.end_season, body.notes)
    _after_commish_change(ctx["league_id"])
    return {"ok": True}


@app.post("/commissioner/invite")
def commissioner_invite(body: CommishInvite, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    if body.role not in ("owner", "co_owner"):
        raise HTTPException(status_code=400, detail="Pick owner or co-owner.")
    return _commish_call(owner_settings.create_invite, client, user_id=user.id, league_id=ctx["league_id"], team_id=body.team_id,
                         email=body.email, role=body.role)


@app.delete("/commissioner/invite/{invite_id}")
def commissioner_cancel_invite(invite_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    try:
        client.table("league_invites").update({"status": "revoked"}).eq("id", invite_id).eq("league_id", ctx["league_id"]).execute()
    except Exception:
        raise HTTPException(status_code=400, detail="That didn't save. Try again.")
    return {"ok": True}


@app.delete("/commissioner/member/{member_user_id}")
def commissioner_remove_member(member_user_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    _commish_call(commissioner.remove_member, client, ctx["league_id"], member_user_id)
    cache.clear()  # the removed person's team lookup is cached by user
    return {"ok": True}


@app.post("/commissioner/switch")
def commissioner_switch(body: CommishSwitch, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    result = _commish_call(commissioner.set_switch, client, ctx["league_id"], body.enabled, body.own_from_week)
    if body.enabled:  # FAAB moves into the app with the switch: each team starts with what it has left on Sleeper
        try:
            commissioner.seed_faab(client, ctx["league_id"], int(ctx["season"]), league.sleeper_league_id(client, ctx["league_id"]))
        except Exception as exc:
            print(f"APP_FAAB_SEED_ERROR {type(exc).__name__}", flush=True)
    cache.clear()
    return {"ok": True, "switch": result}


@app.get("/commissioner/rules")
def commissioner_rules(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    return commissioner.rules_form(client, ctx["league_id"])


@app.post("/commissioner/rules")
def commissioner_save_rules(body: CommishRules, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _commissioner_ctx(client, user)
    _commish_call(commissioner.save_rules, client, ctx["league_id"], body.values, body.scoring)
    _after_commish_change(ctx["league_id"])
    return {"ok": True}


@app.get("/teams/{team_id}")
def team_page(team_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    return _team_page(client, _season_context(client, user), team_id)


@app.get("/my-team")
def my_team(week: int | None = Query(default=None, ge=1, le=18), owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    if not ctx["team_id"]:
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    return _team_page(client, ctx, ctx["team_id"], week)


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
    # Sleeper closes a week only after Monday night, so his game shows from the open week once it kicks off
    # (live, refreshed like the team page) and stays out of his averages until it's final.
    finished: dict[int, dict[str, Any]] = {}
    try:
        finished = player.open_week_games(ctx["season"], str(meta.get("team") or ""), range(weeks + 1, league.current_week() + 1))
    except Exception:
        pass
    open_weeks = sorted(finished)
    league_weeks = teams.weekly_points(ctx["sleeper_id"], max([weeks, *open_weeks]), player_id, open_from=weeks + 1)
    if open_weeks:  # a bye or unplayed open week before a finished one stays out
        league_weeks = [w for w in league_weeks if w["week"] <= weeks or w["week"] in finished]
    if any(w["points"] is None for w in league_weeks) and meta.get("position"):
        try:
            scores = player.weekly_scores(ctx["sleeper_id"], ctx["season"], weeks, player_id, str(meta["position"]), open_weeks)
            league_weeks = player.with_unrostered_points(league_weeks, scores)
        except Exception:
            pass
    live = player.live_weeks(finished)
    deal = (_rookie_deals(client, ctx["league_id"]) or {}).get(player_id)
    result = teams.player_detail(player_id, state, meta, [w for w in league_weeks if w["week"] not in live], ctx["team_id"], deal)
    if result is None:
        raise HTTPException(status_code=404, detail="Player not found.")
    c = result.get("contract")
    extras = player.page_extras(
        client, league_id=ctx["league_id"], sleeper_id=ctx["sleeper_id"], season=ctx["season"], player_id=player_id,
        meta=meta, pos=result["pos"], league_weeks=league_weeks, last_week=weeks,
        years_left={c["team_id"]: c["years_left"]} if c else {}, salary=c["salary"] if c else None,
        player_info=teams.player_info, finished=finished, playoff_weeks=_playoff_weeks(ctx["sleeper_id"]),
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
    return {"season": ctx["season"], **result, **extras, "transactions_enabled": _transactions_on(client, ctx["league_id"]),
            "sleeper_league_id": ctx["sleeper_id"]}


def _playoff_weeks(sleeper_id: str) -> list[int]:
    """The league's playoff weeks from its Sleeper settings (15-17 when unknown)."""
    try:
        settings = (league.sleeper.get(f"league/{sleeper_id}") or {}).get("settings") or {}
        start = int(settings.get("playoff_week_start") or 15)
        teams_in = int(settings.get("playoff_teams") or 4)
        rounds = max(1, (teams_in - 1).bit_length())
        weeks = own_league.round_weeks(start, rounds, int(settings.get("playoff_round_type") or 0))
        return sorted(w for ws in weeks.values() for w in ws)
    except Exception:
        return [15, 16, 17]


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
def box_score(week: int, matchup_id: int, carry: str | None = Query(default=None, max_length=64),
              owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _with_team_names(owner, _box_score_raw(week, matchup_id, carry, owner))


def _box_score_raw(
    week: int,
    matchup_id: int,
    carry: str | None = Query(default=None, max_length=64),
    owner: tuple[Client, Any] = Depends(owner_client),
) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    if not 1 <= week <= 25:
        raise HTTPException(status_code=404, detail="No such week.")
    result = box_score_mod.game(client, ctx, week, matchup_id, _league_team_rows(client, ctx["league_id"]))
    if result is None:
        raise HTTPException(status_code=404, detail="Game not found.")
    if carry:
        box_score_mod.add_round(result, _parse_carry(carry))
    try:  # each team's own picture from Settings (our storage, not Sleeper); the app shows initials without one
        pics = profiles.pictures(profiles.by_roster(client, ctx["league_id"]), {})
        for team in result["teams"]:
            team["avatar"] = pics.get(team["roster_id"])
    except Exception as exc:
        print(f"APP_BOX_PICTURES_ERROR {type(exc).__name__}", flush=True)
    return result


def _parse_carry(raw: str) -> dict[int, float]:
    """'roster:points,roster:points' -> {roster: points} (points banked in earlier weeks of a playoff round)."""
    out: dict[int, float] = {}
    for part in raw.split(","):
        rid, _, pts = part.partition(":")
        try:
            out[int(rid)] = float(pts)
        except ValueError:
            continue
    return out


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
def free_agents(limit: int = Query(default=market.LIST_SIZE, ge=1, le=3000),
                owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    state = _season_state(client, ctx)
    pool, ppg = _market_ppg(client, ctx)
    try:
        rostered = league.sleeper_owners(ctx["sleeper_id"])
    except Exception:
        rostered = {}
    return market.free_agents(pool, state, ppg, limit=limit, rostered=rostered)


@app.get("/market/players")
def all_players(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Every rostered player plus the best free agents, for the Players tab and pull-down search."""
    client, user = owner
    ctx = _season_context(client, user)
    state = _season_state(client, ctx)
    pool, ppg = _market_ppg(client, ctx)
    try:
        rosters = league.sleeper.get(f"league/{ctx['sleeper_id']}/rosters") or []
    except Exception:
        rosters = []
    ids = _saved_ids(client, ctx["league_id"]) or []
    result = market.all_players(pool, state, ppg, _league_team_rows(client, ctx["league_id"]), rosters, ctx["team_id"],
                                keep_ids=ids, kickers=not ctx["slots"] or "K" in ctx["slots"])
    _add_this_week(client, ctx, result["players"])
    # The ★ Saved chip's "Plan these moves with the GM" question.
    result["plan_question"] = market.strategy_question(market.targets(ids, state, ctx["team_id"], pool, ppg), ctx["season"])
    return result


def _add_this_week(client: Client, ctx: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Market rows get this week's numbers (api.market_week, shared with the website's League Players tab)."""
    market_week.add_this_week(client, ctx["sleeper_id"], ctx["season"], rows)


@app.get("/my-team/calls")
def my_calls(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """My keep-or-walk calls on rookie deals, shown on top of My Team from the final game to the June 30 rollover."""
    client, user = owner
    ctx = _season_context(client, user)
    phase, today = _league_phase(ctx)
    if phase != "decisions" or not ctx.get("team_id"):
        return {"calls": None, "sleeper_league_id": ctx["sleeper_id"]}
    state = _season_state(client, ctx)
    pool, ppg = _market_ppg(client, ctx)
    return {"calls": _my_calls(client, ctx, state, pool, ppg, today), "sleeper_league_id": ctx["sleeper_id"]}


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


@app.get("/league/activity")
def league_activity(limit: int = Query(150, ge=1, le=500), owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Every trade, pickup, drop and draft pick, newest first (api/activity.py); the website's Activity page shows the same."""
    client, user = owner
    ctx = _season_context(client, user)
    lid = ctx["league_id"]
    current = (ctx["season"], league.last_scored_week(ctx["sleeper_id"]))

    def build() -> dict[str, Any]:
        states = _seasons_state(client, lid, ctx["season"])
        contracts = {str(r.get("sleeper_player_id")): {"salary": r.get("contract_cap_hit") or r.get("cap_hit"), "years": r.get("contract_years_left")}
                     for r in states[0].get("roster", []) if r.get("sleeper_player_id")}
        return activity.feed(client, lid, ctx["sleeper_id"], current, _league_team_rows(client, lid), contracts, week=league.current_week(),
                             dead_cap=activity.dead_cap_index(states))

    data = cache.cached(("activity", lid, current), 120, build)
    return {"items": data["items"][:limit], "seasons": data["seasons"], "total": len(data["items"]), "me": ctx.get("owner_name")}


# ---- Trades between owners (off until the league leaves Sleeper) -------------------

def _transactions_on(client: Client, league_id: str) -> bool:
    return cache.cached(("tx_on", league_id), 60, lambda: trade_offers.enabled(client, league_id))


def _trade_window(client: Client, league_id: str) -> dict[str, Any]:
    """Open or closed for the league's trade deadline (Settings), shared by the builder, sending and accepting."""
    return cache.cached(("trade_window", league_id), 300, lambda: trade_offers.league_window(client, league_id))



@app.get("/trade-builder/{team_id}")
def trade_builder(team_id: str, add: str | None = None, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Both rosters and pick lists for a trade with one team, with today's dynasty values for the live read."""
    client, user = owner
    ctx = _season_context(client, user)
    if not ctx["team_id"] or team_id == ctx["team_id"]:
        raise HTTPException(status_code=400, detail="Pick another owner's team to trade with.")
    window = _trade_window(client, ctx["league_id"])
    if not window["open"]:  # past the deadline: the app shows the closed screen, so skip the heavy reads
        empty = {"owner_name": "", "cap_space": 0, "players": [], "picks": []}  # older app builds read these
        return {"enabled": _transactions_on(client, ctx["league_id"]), "window": window, "season": ctx["season"],
                "sleeper_league_id": ctx["sleeper_id"], "salary_cap": 0, "add": add,
                "me": {"team_id": ctx["team_id"], **empty}, "them": {"team_id": team_id, **empty}}
    rows, states, _, cap = _league_teams(client, ctx)
    them = next((t for t in rows if t["team_id"] == team_id), None)
    me = next((t for t in rows if t["team_id"] == ctx["team_id"]), None)
    if them is None or me is None:
        raise HTTPException(status_code=404, detail="Team not found in your league.")
    state = states[0]
    ids = [str(r.get("sleeper_player_id")) for r in state.get("roster", []) if str(r.get("league_team_id")) in {team_id, ctx["team_id"]}]
    pricer = cache.cached(("trade_pricer", ctx["league_id"], ctx["season"]), 600, 
                          lambda: trade_offers.build_pricer(client, state, ctx["season"], teams=len(rows),
                                                            records={t["team_id"]: (int(t.get("wins") or 0), int(t.get("losses") or 0)) for t in rows}))
    values = pricer.values
    pick_value = trade_offers.pick_pricer(trade_feed.pick_values(client))
    owner_of = {t["team_id"]: t["owner_name"] for t in rows}
    info = teams.player_info(client, ids)
    ppg = teams.player_ppg(ctx["sleeper_id"], league.last_scored_week(ctx["sleeper_id"]))
    mine = profiles.by_team(client, ctx["league_id"])
    me, them = (profiles.overlay_team(dict(t), mine.get(t["team_id"])) for t in (me, them))
    return {
        "enabled": _transactions_on(client, ctx["league_id"]),
        "window": window,
        "season": ctx["season"],
        "sleeper_league_id": ctx["sleeper_id"],
        "salary_cap": cap,
        # GM read: a dollar of cap space (any season) on the same value scale as players and picks
        "cap_dollar_value": round(pricer.cap_dollar, 2),
        "me": {"team_id": ctx["team_id"], "owner_name": me["owner_name"], "team_name": me.get("team_name"), "avatar": me.get("avatar"),
               "cap_space": me["cap_space"], "cap_by_year": me.get("cap_by_year") or [],
               **trade_offers.side(state, ctx["team_id"], info, ppg, values, pick_value, owner_of, pricer)},
        "them": {"team_id": team_id, "owner_name": them["owner_name"], "team_name": them.get("team_name"), "avatar": them.get("avatar"),
                 "cap_space": them["cap_space"], "cap_by_year": them.get("cap_by_year") or [],
                 **trade_offers.side(state, team_id, info, ppg, values, pick_value, owner_of, pricer)},
        "add": add,
    }


class TradePiece(BaseModel):
    contract_id: str | None = None
    player_id: str | None = None
    name: str | None = None
    pick_id: str | None = None
    label: str | None = None


class TradeOffer(BaseModel):
    to_team_id: str
    give: list[TradePiece] = []
    get: list[TradePiece] = []
    note: str | None = None
    parent_offer_id: str | None = None
    # cap space you send / ask for: a number (this season, older app builds) or {season: amount} for several seasons
    give_cap: float | dict[str, float] = 0
    get_cap: float | dict[str, float] = 0


@app.post("/trade-offers")
def send_trade_offer(body: TradeOffer, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Send an offer (or a counter, with parent_offer_id). The database checks ownership, locks and the league switch."""
    client, user = owner
    ctx = _season_context(client, user)
    if not _trade_window(client, ctx["league_id"])["open"]:
        raise HTTPException(status_code=400, detail=trade_offers.CLOSED)
    pieces = [p for p in [*body.give, *body.get] if (p.contract_id and trade_offers.is_uuid(p.contract_id)) or (p.pick_id and trade_offers.is_uuid(p.pick_id))]
    if len(pieces) != len(body.give) + len(body.get) or not trade_offers.is_uuid(body.to_team_id):
        raise HTTPException(status_code=400, detail="Something in this offer isn't a player or pick.")
    request = trade_offers.offer_request(ctx["league_id"], ctx["team_id"], body.to_team_id, [p.model_dump() for p in body.give],
                                         [p.model_dump() for p in body.get], body.give_cap, body.get_cap, body.note, body.parent_offer_id)
    try:
        result = client.rpc("propose_trade_offer_authenticated", {"p_request": request}).execute().data
    except Exception as exc:
        raise HTTPException(status_code=400, detail=trade_offers.friendly(exc))
    return {"ok": True, **(result or {})}


@app.post("/trade-offers/{offer_id}/{action}")
def answer_trade_offer(offer_id: str, action: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """accept | decline | cancel. Accepting completes the trade right away (no review)."""
    client, user = owner
    if action not in {"accept", "decline", "cancel"} or not trade_offers.is_uuid(offer_id):
        raise HTTPException(status_code=400, detail="Unknown action.")
    ctx = resolve_team(client, user)
    if action == "accept" and not _trade_window(client, ctx["league_id"])["open"]:
        raise HTTPException(status_code=400, detail=trade_offers.CLOSED)
    try:
        result = client.rpc("respond_trade_offer_authenticated", {"p_request": {"offer_id": offer_id, "action": action}}).execute().data
    except Exception as exc:
        raise HTTPException(status_code=400, detail=trade_offers.friendly(exc))
    if action == "accept":
        cache.forget("state", ctx["league_id"])
        cache.forget("activity", ctx["league_id"])
    return {"ok": True, **(result or {})}


@app.get("/transactions")
def transactions(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """My Team's transaction center: offers to answer, trades you sent, bids, and the last week's results."""
    client, user = owner
    team = resolve_team(client, user)
    on = _transactions_on(client, team["league_id"])
    empty = {"enabled": on, "needs_answer": [], "sent": [], "bids": [], "results": [], "pending": 0}
    if not on or not team.get("team_id"):
        return empty
    try:
        rows = (client.table("trade_offers").select("*").eq("league_id", team["league_id"])
                .order("created_at", desc=True).limit(200).execute().data or [])
    except Exception:
        return empty
    owner_of = {t["team_id"]: t["owner_name"] for t in _league_team_rows(client, team["league_id"])}
    ids = [str(i.get("player_id")) for r in rows for i in (r.get("players") or []) if i.get("player_id")]
    data = trade_offers.center(rows, str(team["team_id"]), owner_of, teams.player_info(client, ids))
    data["results"] = [{**r, "line": trade_offers.result_line(r)} for r in data["results"]]
    try:
        claims = (client.table("app_waiver_claims").select("*").eq("league_id", team["league_id"])
                  .order("created_at", desc=True).limit(300).execute().data or [])
    except Exception:
        claims = []
    bid_ids = [str(c.get(k)) for c in claims for k in ("player_id", "drop_player_id") if c.get(k)]
    bids = waivers.bids_view(claims, str(team["team_id"]), owner_of, teams.player_info(client, bid_ids))
    results = sorted(data["results"] + bids["results"], key=lambda r: str(r.get("decided_at") or ""), reverse=True)
    return {"enabled": on, **data, "bids": bids["bids"], "results": results, "pending": data["pending"] + len(bids["bids"])}


# ---- Pickups and drops ------------------------------------------------------------


def _process_due_waivers(client: Client, league_id: str) -> None:
    """Run any bids whose Wednesday/Sunday 3 AM time has passed (the scheduled job does the same; first one wins)."""
    def run() -> dict[str, Any]:
        if not _transactions_on(client, league_id):
            return {}
        out = client.rpc("process_due_waivers", {"p_league_id": league_id}).execute().data or {}
        if (out or {}).get("processed"):
            cache.forget("state", league_id)
        return out
    try:
        cache.cached(("waivers_due", league_id), 60, run)
    except Exception:
        pass


def _dead_cap_pct(client: Client, league_id: str) -> float:
    def load() -> dict[str, Any]:
        rows = client.table("league_rules").select("default_dead_cap_pct").eq("league_id", league_id).limit(1).execute().data or []
        return {"pct": float(rows[0]["default_dead_cap_pct"]) if rows and rows[0].get("default_dead_cap_pct") is not None else 50.0}
    return cache.cached(("dead_cap_pct", league_id), 600, load)["pct"]


@app.get("/waivers/player/{player_id}")
def waiver_sheet(player_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """Everything the Bid / Add sheet needs: FAAB left, the next run, my open bid, and my roster with each drop's dead cap."""
    client, user = owner
    ctx = _season_context(client, user)
    state = _my_state(client, ctx)
    lid, me, season = ctx["league_id"], str(ctx["team_id"]), ctx["season"]
    now = datetime.now(timezone.utc)
    ledger = client.table("faab_ledger").select("amount").eq("league_id", lid).eq("league_team_id", me).eq("season", season).execute().data or []
    mine = (client.table("app_waiver_claims").select("id,bid,drop_player_id").eq("league_team_id", me)
            .eq("player_id", player_id).eq("status", "pending").limit(1).execute().data or [])
    roster = [r for r in state.get("roster", []) if str(r.get("league_team_id")) == me and r.get("contract_agreement_id")]
    ids = [str(r["contract_agreement_id"]) for r in roster]
    seasons = client.table("contract_seasons").select("contract_id,season,cap_hit,salary,obligation_status").in_("contract_id", ids).execute().data if ids else []
    by_contract: dict[str, list[dict[str, Any]]] = {}
    for row in seasons or []:
        by_contract.setdefault(str(row["contract_id"]), []).append(row)
    pct = _dead_cap_pct(client, lid)
    info = teams.player_info(client, [r.get("sleeper_player_id") for r in roster])
    starts = _week_kickoffs(client, season, league.current_week())
    players = []
    for r in roster:
        pid = str(r.get("sleeper_player_id"))
        cost = waivers.dead_cap(by_contract.get(str(r["contract_agreement_id"]), []), season, pct)
        players.append({"player_id": pid, "name": r.get("player_name") or info.get(pid, {}).get("full_name") or pid,
                        "pos": r.get("pos") or info.get(pid, {}).get("position") or "", "nfl_team": info.get(pid, {}).get("team") or "FA",
                        "salary": float(r.get("contract_cap_hit") or r.get("cap_hit") or 0), "dead_cap": cost,
                        "dead_cap_text": waivers.dead_cap_text(cost, season), "designation": teams.roster_designation(r),
                        "locked": lineups.is_locked(pid, info.get(pid, {}).get("team"), starts, now)})
    players.sort(key=lambda p: (p["dead_cap"]["total"], p["salary"]))
    slots = len([s for s in (_roster_positions(client, lid)) if s not in ("TAXI", "IR")])
    active = sum(1 for p in players if not p["designation"])
    return {
        "enabled": _transactions_on(client, lid), "season": season, "faab_left": round(sum(float(r["amount"]) for r in ledger), 2),
        "free_agency_open": waivers.free_agency_open(now), "next_run": waivers.run_label(waivers.next_run(now)),
        "my_bid": ({"id": str(mine[0]["id"]), "bid": float(mine[0]["bid"]), "drop_player_id": mine[0].get("drop_player_id")} if mine else None),
        "roster_full": bool(slots) and active >= slots, "players": players,
        "locked": lineups.is_locked(player_id, teams.player_info(client, [player_id]).get(player_id, {}).get("team"), starts, now),
    }


def _roster_positions(client: Client, league_id: str) -> list[str]:
    def load() -> dict[str, Any]:
        from league_ai.settings_store import load_settings
        return {"slots": list(load_settings(client, league_id).roster_positions or [])}
    return cache.cached(("roster_positions", league_id), 600, load)["slots"]


class BidRequest(BaseModel):
    player_id: str
    bid: int
    drop_player_id: str | None = None


class AddRequest(BaseModel):
    player_id: str
    drop_player_id: str | None = None


class DropRequest(BaseModel):
    player_id: str


def _waiver_call(client: Client, user: Any, fn: str, request: dict[str, Any]) -> dict[str, Any]:
    ctx = _season_context(client, user)
    if not ctx["team_id"]:
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    try:
        out = client.rpc(fn, {"p_request": {"league_id": ctx["league_id"], **request}}).execute().data or {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=waivers.friendly(exc))
    for key in ("state", "activity"):
        cache.forget(key, ctx["league_id"])
    return out


@app.post("/waivers/bid")
def place_bid(body: BidRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _waiver_call(*owner, "place_waiver_claim_authenticated", body.model_dump())


@app.post("/waivers/bid/{claim_id}/cancel")
def cancel_bid(claim_id: str, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    if not trade_offers.is_uuid(claim_id):
        raise HTTPException(status_code=404, detail="Bid not found.")
    client, user = owner
    try:
        return client.rpc("cancel_waiver_claim_authenticated", {"p_request": {"claim_id": claim_id}}).execute().data or {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=waivers.friendly(exc))


@app.post("/waivers/add")
def add_free_agent(body: AddRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _waiver_call(*owner, "add_free_agent_authenticated", body.model_dump())


@app.post("/my-team/drop")
def drop_player(body: DropRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _waiver_call(*owner, "drop_player_authenticated", body.model_dump())


# ---- Front Office ------------------------------------------------------------------


class ChatImage(BaseModel):
    media_type: str = "image/jpeg"
    data: str  # base64


class ChatRequest(BaseModel):
    question: str = ""
    conversation_id: str | None = None
    images: list[ChatImage] | None = None  # screenshots (up to 10): a trade offer, a roster, another league


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


def _chat_images(body: ChatRequest) -> list[screenshots.Shot]:
    try:
        return screenshots.clean([i.model_dump() for i in body.images or []])
    except screenshots.ScreenshotError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _chat_session(body: ChatRequest, client: Client, user: Any) -> tuple[front_office.Session, str]:
    question = body.question.strip()
    if not question and not body.images:
        raise HTTPException(status_code=400, detail="Ask a question first.")
    if len(question) > 2000:
        raise HTTPException(status_code=400, detail="That question is too long. Keep it under 2,000 characters.")
    if not front_office.ai_ready():
        raise HTTPException(status_code=503, detail="The Assistant GM isn't switched on for the app yet.")
    session = _front_office(client, user)
    if not front_office.take_question(session.owner.user_id, client=client):
        raise HTTPException(status_code=429, detail=f"That's {front_office.DAILY_QUESTIONS} questions today. The Assistant GM is back tomorrow.")
    return session, question


@app.post("/front-office/chat")
def front_office_chat(body: ChatRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    images = _chat_images(body)
    session, question = _chat_session(body, *owner)
    return front_office.ask(session, question, body.conversation_id, images=images or None)


@app.post("/front-office/chat/stream")
def front_office_chat_stream(body: ChatRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> StreamingResponse:
    """The same answer as /front-office/chat, streamed: what the GM is checking, then the answer as it's written."""
    images = _chat_images(body)
    session, question = _chat_session(body, *owner)
    return StreamingResponse(front_office.ask_stream(session, question, body.conversation_id, images or None), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class ChatAddRequest(BaseModel):
    text: str


@app.post("/front-office/chat/add")
def front_office_chat_add(body: ChatAddRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """More context typed while the GM is still answering: it goes into that same answer.
    {"added": false} means the answer already finished, so the app sends it as a new question instead."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type something first.")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="That's too long. Keep it under 2,000 characters.")
    return {"added": front_office.tell(_front_office(*owner), text)}


@app.post("/front-office/chat/resume")
def front_office_chat_resume(owner: tuple[Client, Any] = Depends(owner_client)) -> StreamingResponse:
    """The owner's latest answer, replayed and followed to the end: for coming back after the app closed mid-answer.
    No AI call and no daily-question charge here; the answer was already paid for."""
    session = _front_office(*owner)
    return StreamingResponse(front_office.resume_stream(session), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class MoveRequest(BaseModel):
    conversation_id: str
    option: str | None = None  # the button tapped; left out = the GM's pick


@app.post("/front-office/moves/{move_id}/{action}")
def front_office_move(move_id: str, action: str, body: MoveRequest, owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    """confirm (with the option tapped) | dismiss a move card the GM proposed. Confirm runs the same checked database call as the owner's own tap."""
    if action not in {"confirm", "dismiss"} or not trade_offers.is_uuid(move_id) or not trade_offers.is_uuid(body.conversation_id):
        raise HTTPException(status_code=400, detail="Unknown action.")
    session = _front_office(*owner)
    try:
        card = front_office.settle_move(session, body.conversation_id, move_id, action, body.option)
    except gm_moves.MoveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if card["status"] == gm_moves.DONE:
        for key in ("state", "activity"):
            cache.forget(key, session.owner.league_id)
    return {"ok": card["status"] == gm_moves.DONE, "move": card}


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


def _nfl_week(client: Client, season: int, week: int) -> list[dict[str, Any]]:
    try:
        return cache.cached(("nfl_games", season, week), 600, lambda: client.table("nfl_games").select("gameday,gametime,home_team,away_team")
                            .eq("season", season).eq("week", week).execute().data or [])
    except Exception:
        return []


def _risers(client: Client, ctx: dict[str, Any], week: int) -> list[dict[str, Any]]:
    """Highest projected for the league this week (the same list for every owner; cached half an hour)."""
    sid, season = ctx["sleeper_id"], ctx["season"]
    lines = projections.week_lines("projections", season, week, 1800)
    scoring = projections.league_scoring(sid)
    skill = [pid for pid, line in lines.items() if line.get("pos") in ("QB", "RB", "WR", "TE")
             and projections.score(line["stats"], scoring) >= home.RISER_MIN_PROJ * 0.8]
    proj = projections.week_projections(client, sid, season, week, skill, {p: lines[p]["pos"] for p in skill}, {p: lines[p]["team"] for p in skill})
    # The average is in league scoring too (the market's PPG is full PPR, which made QBs look like risers).
    avg, _games = projections.season_averages(sid, season, week - 1) if week > 1 else ({}, {})
    near = [p for p, v in proj.items() if v >= home.RISER_MIN_PROJ]
    info = teams.player_info(client, near)
    last_lines = projections.week_lines("stats", season, week - 1, 86400) if week > 1 else {}
    last = {p: projections.score(last_lines[p]["stats"], scoring) for p in near if p in last_lines}
    try:
        owners = league.sleeper_owners(sid)
    except Exception:
        owners = {}
    return home.top_projected({p: proj[p] for p in near}, avg, info, owners, ctx["names"], None, last)


def _league_live(client: Client, ctx: dict[str, Any], week: int, starts: dict[str, datetime], now: datetime) -> dict[int, dict[str, Any]]:
    """Every Legacy team's score now and projected final this week (Shotgun watch and the top-5 line)."""
    sid = ctx["sleeper_id"]
    raw = league.sleeper.get(f"league/{sid}/matchups/{week}", ttl=league.LIVE_TTL) or []
    ids = [p for r in raw for p in (r.get("starters") or []) if p and p != "0"]
    info = teams.player_info(client, ids)
    proj = cache.cached(("starter_proj", sid, ctx["season"], week), 1800, lambda: projections.week_projections(
        client, sid, ctx["season"], week, ids, {p: _text(info.get(p, {}).get("position")) for p in ids},
        {p: _text(info.get(p, {}).get("team")) for p in ids}))
    try:
        active = cache.cached(("starter_proj_active", sid, ctx["season"], week), 1800, lambda: projections.week_projections(
            client, sid, ctx["season"], week, ids, {p: _text(info.get(p, {}).get("position")) for p in ids},
            {p: _text(info.get(p, {}).get("team")) for p in ids}, active=True))
    except Exception:
        active = {}
    return home.team_live(raw, info, starts, now, proj, active)


def _game_day_bottom(client: Client, ctx: dict[str, Any], user: Any, week: int, current: dict[str, Any],
                     pics: dict[int, str | None], now: datetime, playoffs: bool = False) -> dict[str, Any] | None:
    """The bottom card of game-day Home: Highest projected, the week's scoreboard, or Shotgun watch.

    In the playoffs Shotgun watch runs on its usual days (every team still scores and last place still shotguns) and
    skips the scoreboard, since Sleeper's pairings for playoff weeks aren't our bracket games.
    """
    sid, my_roster = ctx["sleeper_id"], ctx["sleeper_roster_id"]
    starts = home.kickoffs(_nfl_week(client, ctx["season"], week))
    pick = home.bottom_mode(now, starts)
    if playoffs:
        # Shotgun Watch only when the regular season shows it: Sunday's late games through Monday (Tommy,
        # 2026-11-18). Other days the playoff Home has no bottom card from here.
        if pick["mode"] != "shotgun":
            return None
        live = _league_live(client, ctx, week, starts, now)
        return {"mode": "shotgun", "shotgun": home.shotgun(live, ctx["names"], pics, my_roster, pick["window"]), "scoreboard": []}
    if pick["mode"] == "risers":
        # Mode stays "risers" so older app builds still render the card. The GM's Risers grading log
        # (league_ai/risers.py) is no longer fed: a top-projected list isn't a "beats his average" call.
        rows = cache.cached(("top_proj", sid, ctx["season"], week), 1800, lambda: _risers(client, ctx, week))
        return {"mode": "risers", "risers": [{**r, "is_mine": r["roster_id"] is not None and r["roster_id"] == my_roster} for r in rows]}

    live = _league_live(client, ctx, week, starts, now)
    if pick["mode"] == "scoreboard":
        return {"mode": "scoreboard", "scoreboard": home.scoreboard(current["games"], live, my_roster, pics),
                "race": home.race(current["games"], my_roster, pics)}
    return {"mode": "shotgun", "shotgun": home.shotgun(live, ctx["names"], pics, my_roster, pick["window"]),
            "scoreboard": home.scoreboard(current["games"], live, my_roster, pics)}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _home_week(client: Client, season: int, today: date) -> int:
    """Home's week from our own NFL schedule (it holds Monday night's week until midnight
    Pacific); Sleeper's week only when the schedule isn't loaded or the season is over."""
    def load() -> dict[int, str]:
        rows = client.table("nfl_games").select("week,gameday").eq("season", season).execute().data or []
        last: dict[int, str] = {}
        for r in rows:
            wk, day = r.get("week"), str(r.get("gameday") or "")[:10]
            if wk is not None and int(wk) <= 18 and day and day > last.get(int(wk), ""):
                last[int(wk)] = day
        return last
    try:
        week = home.schedule_week(cache.cached(("nfl_gamedays", season), 3600, load), today)
    except Exception as exc:
        print(f"APP_HOME_WEEK_ERROR {type(exc).__name__}", flush=True)
        week = None
    return week or league.current_week()


@app.get("/home")
def home_page(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    return _with_team_names(owner, _home_raw(owner))


def _home_raw(owner: tuple[Client, Any] = Depends(owner_client)) -> dict[str, Any]:
    client, user = owner
    ctx = _season_context(client, user)
    if not ctx.get("team_id"):
        raise HTTPException(status_code=404, detail="This account isn't linked to a team yet.")
    sid, names, my_roster = ctx["sleeper_id"], ctx["names"], ctx["sleeper_roster_id"]
    sleeper_league = league.sleeper.get(f"league/{sid}") or {}
    try:  # our playoff format from Settings, not Sleeper's
        rules = cache.cached(("playoff_rules", ctx["league_id"]), 300, lambda: league.playoff_rules(client, ctx["league_id"]))
        playoff_start = int(rules["playoff_week_start"])
    except Exception:
        rules = None
        playoff_start = int((sleeper_league.get("settings") or {}).get("playoff_week_start") or 15)
    today = datetime.now(home.HOME_TZ).date()
    week = _home_week(client, ctx["season"], today)
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
            pics = _pictures(client, ctx["league_id"], sid)
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
            nfl = _nfl_week(client, ctx["season"], wk)
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
        if phase in ("game_day", "playoffs"):
            try:  # the projected 5th-best final score this week (1 standings point for a top-5 week)
                now_mt = datetime.now(front_office.LEAGUE_TZ)
                live_all = _league_live(client, ctx, week, home.kickoffs(_nfl_week(client, ctx["season"], week)), now_mt)
                out["top5_line"] = home.top5_line(live_all, final=current["is_final"])
            except Exception as exc:
                print(f"APP_HOME_TOP5_ERROR {type(exc).__name__}", flush=True)
        if phase in ("game_day", "playoffs"):
            # The bottom card (Shotgun Watch late in the week) runs in the playoffs too: every team still scores.
            try:
                out["bottom"] = _game_day_bottom(client, ctx, user, week, current, pics, datetime.now(front_office.LEAGUE_TZ),
                                                 playoffs=phase == "playoffs")
            except Exception as exc:  # the old standings / race cards still show
                print(f"APP_HOME_BOTTOM_ERROR {type(exc).__name__}", flush=True)
        if phase == "game_day":
            them = (mine_now or {}).get("them")
            if them:
                out["series"] = teams.head_to_head(sid, weeks, my_roster, them["roster_id"])
    def recap(wk: int) -> dict[str, Any]:
        last = league.matchups(sid, names, wk, my_roster)
        game = home.my_game(last["games"], my_roster)
        recap_players = lineups(wk, game, True)["me"]
        return {
            "week": wk, "game": game_view(game), "summary": last["summary"],
            "top_scorers": sorted((p for p in recap_players if p["player_id"]), key=lambda p: -p["points"])[:3],
            "standing_points": home.standing_points_earned(game),
        }

    if phase == "recap" and week > 1:
        out["recap"] = recap(week - 1)
        them = (mine_now or {}).get("them")
        if them:
            out["series"] = teams.head_to_head(sid, weeks, my_roster, them["roster_id"])
        out["weeks_left"] = max(0, playoff_start - week)
    if phase == "playoffs":
        # Our own bracket (seeds from our standings, format from Settings); Sleeper is only the score feed.
        try:
            view = league.playoff_view(sid, names, week, my_roster, rules, pics) if rules else None
        except Exception as exc:
            print(f"APP_HOME_PLAYOFFS_ERROR {type(exc).__name__}", flush=True)
            view = None
        out["bracket"] = home.bracket_from_view(view)
        out["playoff_game"] = home.playoff_game(view)
        if view and not out["playoff_game"] and any(t.get("roster_id") == my_roster for t in view.get("byes") or []):
            try:  # my consolation bye: both bye teams' scores still count, so they get projections too
                playoffs.add_projections(view, _week_projections(client, user, view["week"]), week_final=bool(current["is_final"]))
            except Exception as exc:
                print(f"APP_HOME_BYE_PROJ_ERROR {type(exc).__name__}", flush=True)
            out["playoff_bye"] = home.playoff_bye(view, my_roster)
        out["champion"] = home.champion(view, my_roster)
        if view and today.weekday() in (1, 2):
            # Tuesday/Wednesday: the next rookie draft's board, with the playoff teams' picks still moving.
            try:
                wp = _week_projections(client, user, view["week"])
                playoffs.add_projections(view, wp)
                strength = {rid: float(v.get("projected") or 0.0) for rid, v in wp.items()}
                out["draft_board"] = home.draft_board(
                    standings_rows, int(rules["playoff_teams"]) if rules else league.PLAYOFF_TEAMS, playoffs.finish_odds(view, strength),
                    state.get("draft_picks", []), team_ids, names, pics, int(ctx["season"]) + 1, my_roster)
            except Exception as exc:
                print(f"APP_HOME_DRAFT_BOARD_ERROR {type(exc).__name__}", flush=True)
        # Tuesday in the playoffs is last week's final, the same as the regular season (Tommy, 2026-11-22):
        # the week before the playoffs is a normal recap; a playoff week also carries last week's playoff game.
        if today.weekday() == 1 and week > 1:
            try:
                out["recap"] = recap(week - 1)
                if week - 1 >= playoff_start and rules:
                    prev = league.playoff_view(sid, names, week - 1, my_roster, rules, pics)
                    out["recap"]["playoff_game"] = home.playoff_game(prev)
            except Exception as exc:
                print(f"APP_HOME_PLAYOFF_RECAP_ERROR {type(exc).__name__}", flush=True)
    if phase in ("decisions", "draft_prep") and rules:
        # The champion card stays on Home for CHAMPION_DAYS after the title game's last kickoff day.
        try:
            last = playoffs.last_week(rules)
            starts = home.kickoffs(_nfl_week(client, ctx["season"], last))
            ended = max(starts.values()).astimezone(home.HOME_TZ).date() if starts else None
            if ended and (today - ended).days <= home.CHAMPION_DAYS:
                pics = pics or _pictures(client, ctx["league_id"], sid)
                out["champion"] = home.champion(league.playoff_view(sid, names, last, my_roster, rules, pics), my_roster)
        except Exception as exc:
            print(f"APP_HOME_CHAMPION_ERROR {type(exc).__name__}", flush=True)
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
