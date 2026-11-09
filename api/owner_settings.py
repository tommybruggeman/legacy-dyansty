"""Settings for each owner, behind the initials circle on Home (and the same things on the website):
their profile (name, team name, picture), Front Office preferences, their co-owner, and the league's rules to read.

Everything runs as the signed-in owner, so the database's row-level security and RPCs decide who can change what
(supabase/migrations/20261219_owner_settings.sql).
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import quote

from api import cache, profiles

ANSWER_LENGTHS = ("short", "detailed")
TEAM_PLANS = ("auto", "contend", "rebuild")
INVITE_DAYS = 14
WEBSITE = "https://legacy-dynasty.streamlit.app"

ERRORS = {
    "profile_not_yours": "You can only change your own team.",
    "profile_team_invalid": "That team isn't in this league.",
    "profile_picture_invalid": "That picture didn't upload. Try again.",
    "profile_abbr_invalid": "The short name has to be 2 to 4 letters or numbers.",
    "co_owner_not_allowed": "Only the team's owner can do that.",
    "co_owner_not_found": "That person isn't a co-owner on your team.",
    "row-level security": "Only the team's owner can do that.",
}


def friendly(exc: Exception) -> str:
    text = f"{exc} {getattr(getattr(exc, 'response', None), 'text', '')}"
    for code, message in ERRORS.items():
        if code in text:
            return message
    return "That didn't save. Try again."


# ---- profile ------------------------------------------------------------------------------------------

def profile_view(client: Any, team: Mapping[str, Any]) -> dict[str, Any]:
    mine = profiles.by_team(client, team["league_id"]).get(str(team.get("team_id"))) or {}
    return {
        "display_name": mine.get("display_name") or team.get("owner_name") or "",
        "team_name": mine.get("team_name") or team.get("team_name") or "",
        "team_abbr": mine.get("team_abbr") or profiles.abbr(mine.get("team_name") or team.get("team_name") or ""),
        "picture_url": mine.get("picture_url"),
    }


def save_profile(client: Any, team: Mapping[str, Any], body: Mapping[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {
        "league_team_id": team["team_id"],
        "display_name": profiles.clean(body.get("display_name")),
        "team_name": profiles.clean(body.get("team_name")),
        "team_abbr": "".join(ch for ch in str(body.get("team_abbr") or "").upper() if ch.isalnum())[:4] or None,
    }
    if "picture_url" in body:  # left out = keep the picture; null = remove it
        request["picture_url"] = body.get("picture_url") or None
    try:
        client.rpc("save_owner_profile_authenticated", {"p_request": request}).execute()
    except Exception as exc:
        raise ValueError(friendly(exc)) from None
    forget_names(team["league_id"])
    return profile_view(client, team)


def forget_names(league_id: str) -> None:
    """New names and pictures show on the next page load, not in ten minutes."""
    cache.forget("profiles", league_id)
    cache.forget("names", league_id)
    cache.forget("team_labels", league_id)


# ---- Front Office preferences -------------------------------------------------------------------------

def gm_prefs(client: Any, user_id: str) -> dict[str, str]:
    try:
        rows = client.table("owner_gm_prefs").select("answer_length,team_plan").eq("user_id", user_id).limit(1).execute().data or []
    except Exception:
        rows = []
    row = rows[0] if rows else {}
    return {"answer_length": row.get("answer_length") or "short", "team_plan": row.get("team_plan") or "auto"}


def save_gm_prefs(client: Any, user_id: str, body: Mapping[str, Any]) -> dict[str, str]:
    current = gm_prefs(client, user_id)
    length = body.get("answer_length") or current["answer_length"]
    plan = body.get("team_plan") or current["team_plan"]
    if length not in ANSWER_LENGTHS or plan not in TEAM_PLANS:
        raise ValueError("Pick one of the options.")
    row = {"user_id": user_id, "answer_length": length, "team_plan": plan, "updated_at": datetime.now(timezone.utc).isoformat()}
    try:
        client.table("owner_gm_prefs").upsert(row, on_conflict="user_id").execute()
    except Exception as exc:
        raise ValueError(friendly(exc)) from None
    cache.forget("gm_prefs", user_id)
    return {"answer_length": length, "team_plan": plan}


# ---- co-owner -----------------------------------------------------------------------------------------

def people(client: Any, team: Mapping[str, Any]) -> dict[str, Any]:
    """Who is on my team (owner, co-owners) and any co-owner invite still waiting."""
    try:
        members = client.rpc("my_team_people_authenticated", {}).execute().data or []
    except Exception:
        members = []
    invites: list[dict[str, Any]] = []
    try:
        now = datetime.now(timezone.utc).isoformat()
        rows = (client.table("league_invites").select("id,email,expires_at,status")
                .eq("league_team_id", team["team_id"]).eq("role", "co_owner").eq("status", "pending")
                .gt("expires_at", now).execute().data or [])
        invites = [{"id": r["id"], "email": r["email"], "expires_at": r.get("expires_at")} for r in rows]
    except Exception:
        pass
    co_owners = [{"user_id": m["user_id"], "email": m.get("email"), "is_me": bool(m.get("is_me"))}
                 for m in members if str(m.get("role") or "").lower() in ("co_owner", "co-owner")]
    return {"co_owners": co_owners, "invites": invites, "can_invite": not co_owners and not invites}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_invite(client: Any, *, user_id: str, league_id: str, team_id: str, email: str, role: str) -> dict[str, Any]:
    """A pending invite row (only the token's hash is stored, like the website's) and the sign-up link to share.
    The same token format as services/invitations.py, so the website's sign-up accepts it."""
    address = (email or "").strip().lower()
    if not EMAIL_RE.match(address):
        raise ValueError("That email doesn't look right.")
    token = secrets.token_urlsafe(32)
    row = {
        "league_id": league_id, "league_team_id": team_id, "email": address, "role": role,
        "token": None, "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(), "status": "pending", "invited_by": user_id,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat(), "last_sent_at": None, "send_count": 0,
    }
    try:
        client.table("league_invites").insert(row).execute()
    except Exception as exc:
        raise ValueError(friendly(exc)) from None
    return {"email": address, "link": f"{WEBSITE}?invite_token={quote(token)}"}


def invite_co_owner(client: Any, user_id: str, team: Mapping[str, Any], email: str) -> dict[str, Any]:
    """A co-owner invite for my team. Returns the link to send them (it opens the website's sign-up)."""
    if not people(client, team)["can_invite"]:
        raise ValueError("Your team already has a co-owner or an invite waiting.")
    return create_invite(client, user_id=user_id, league_id=team["league_id"], team_id=team["team_id"], email=email, role="co_owner")


def cancel_invite(client: Any, team: Mapping[str, Any], invite_id: str) -> None:
    try:
        client.table("league_invites").update({"status": "revoked"}).eq("id", invite_id).eq("league_team_id", team["team_id"]).execute()
    except Exception as exc:
        raise ValueError(friendly(exc)) from None


def remove_co_owner(client: Any, member_user_id: str) -> None:
    try:
        client.rpc("remove_co_owner_authenticated", {"p_user_id": member_user_id}).execute()
    except Exception as exc:
        raise ValueError(friendly(exc)) from None


# ---- league rules (read-only for owners) --------------------------------------------------------------

def _money(v: Any) -> str:
    n = float(v or 0)
    return f"${n:,.0f}" if n == int(n) else f"${n:,.2f}"


def _pct(v: Any) -> str:
    return f"{float(v or 0):g}%"


def _yes(v: Any) -> str:
    return "Yes" if v else "No"


def _pts(v: Any) -> str:
    n = float(v or 0)
    return f"{n:+g}"


def rules_view(client: Any, league_id: str) -> dict[str, Any]:
    """The league's rules in plain groups, from the same places the website's League Rules form saves to."""
    from league_ai.settings_store import load_settings
    from scoring.rules import CATALOG

    def load() -> dict[str, Any]:
        rows = client.table("league_rules").select("*").eq("league_id", league_id).limit(1).execute().data or []
        r = rows[0] if rows else {}
        ai = load_settings(client, league_id)
        groups = [
            {"title": "Cap and contracts", "rows": [
                ["Salary cap", _money(r.get("salary_cap", 225))],
                ["Longest contract", f"{int(r.get('max_contract_years') or 4)} years"],
                ["Dead cap when you drop someone", _pct(r.get("default_dead_cap_pct", 50))],
                ["$1 deals carry dead cap", _yes(not ai.one_dollar_deals_no_dead_cap)],
            ]},
            {"title": "Roster, IR and taxi", "rows": [
                ["Roster size (with IR and taxi)", str(ai.roster_max)],
                ["IR spots", str(ai.ir_limit)],
                ["IR counts against the cap", _pct(ai.ir_cap_fraction * 100)],
                ["Taxi spots", str(ai.taxi_limit)],
                ["Taxi counts against the cap", _pct(ai.taxi_cap_fraction * 100)],
                ["Taxi is rookies only", _yes(ai.taxi_rookies_only)],
                ["Taxi player sits the whole season", _yes(ai.taxi_locked_full_season)],
                ["Taxi year doesn't use a contract year", _yes(ai.taxi_skips_contract_year)],
            ]},
            {"title": "Free agency and waivers", "rows": [
                ["Waiver salary", "Your FAAB bid" if ai.faab_dollar_per_salary == 1 else f"{ai.faab_dollar_per_salary:g}x your bid"],
                ["A $0 bid signs at", _money(ai.faab_zero_counts_as)],
                ["Waiver pickups last", f"{ai.faab_pickup_years} season" + ("s" if ai.faab_pickup_years != 1 else "")],
                ["Auction minimum, 2 years", _money(r.get("min_2_year_bid", 4))],
                ["Auction minimum, 3 years", _money(r.get("min_3_year_bid", 12))],
                ["Auction minimum, 4 years", _money(r.get("min_4_year_bid", 20))],
            ]},
            {"title": "Rookies", "rows": [
                ["Rookie contract", f"{int(r.get('rookie_contract_years') or 3)} years"],
                ["Team option", f"{int(r.get('rookie_option_years') or 1)} more year" + ("s" if int(r.get('rookie_option_years') or 1) != 1 else "")],
                ["Option decision deadline", "June 30 rollover"],
                ["Draft order", "Reverse of last season's finish"],
            ]},
            {"title": "Standings, trades and playoffs", "rows": [
                ["Points for a win", str(ai.win_points)],
                ["Top-scorer bonus", f"{ai.top_scorer_bonus_points} to the top {ai.top_scorer_bonus_count} each week"],
                ["Tiebreaker", ai.standings_tiebreaker or "Points for"],
                ["Trade deadline", ai.trade_deadline or "None"],
                ["Teams in one trade (max)", str(ai.max_trade_teams)],
                ["Playoff teams", str(ai.playoff_teams)],
                ["Playoffs start", f"Week {ai.playoff_week_start}"],
                ["Weeks per round", str(ai.playoff_round_weeks)],
            ]},
        ]
        scoring = dict(ai.scoring_settings or {})
        for title, rules in CATALOG:
            rows_ = [[label, _pts(scoring[key])] for key, label in rules if float(scoring.get(key) or 0) != 0]
            if rows_:
                groups.append({"title": f"Scoring: {title.lower()}", "rows": rows_})
        if (ai.roster_positions or []):
            starters = [p for p in ai.roster_positions if p not in ("BN", "IR", "TAXI")]
            groups.insert(2, {"title": "Starting lineup", "rows": [["Starters", " · ".join(starters)]]})
        return {"groups": groups, "house_rules": (ai.house_rules or "").strip() or None}

    return cache.cached(("rules_view", league_id), 300, load)
