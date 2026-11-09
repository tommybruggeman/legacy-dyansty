"""Commissioner lineups for the weeks the app runs itself (api/own_league.py): set any team's lineup for an owner
who's away, under the same slot rules and kickoff locks as the owner (commissioner_set_lineup_authenticated).

Scores are never edited by hand. Stat corrections come in automatically with the official stats
(scripts/final_scores.py), and the whole league gets a push when one changes a score.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

ERRORS = {
    "Commissioner authority required": "Only the commissioner can do that.",
    "lineup_team_invalid": "That team isn't in this league.",
}


def friendly(exc: Exception) -> str:
    from api import lineups
    text = str(exc)
    for code, message in {**ERRORS, **lineups.ERRORS}.items():
        if code in text:
            return message
    return "That didn't save. Try again."


def _call(client: Any, fn: str, request: Mapping[str, Any]) -> Any:
    try:
        return client.rpc(fn, {"p_request": dict(request)}).execute().data
    except Exception as exc:
        body = getattr(getattr(exc, "response", None), "text", "")  # the database's error code is in the body
        raise ValueError(friendly(Exception(f"{exc} {body}"))) from None


def set_lineup(client: Any, *, league_id: str, team_id: str, season: int, week: int, slots: list[str],
               player_ids: list[str | None]) -> Any:
    """Any team's starters for a week, in slot order."""
    return _call(client, "commissioner_set_lineup_authenticated", {
        "league_id": league_id, "team_id": team_id, "season": season, "week": week,
        "slots": [{"slot": s, "player_id": player_ids[i] or None} for i, s in enumerate(slots)]})


# ---- Settings > Commissioner (app) ---------------------------------------------------------------------------------
# Same database calls as the website's League Tools, all checked server side for the commissioner role.

TOOL_ERRORS = {
    "contract_not_live": "That contract isn't active anymore.",
    "contract_salary_invalid": "Salary has to be between $0 and $1,000.",
    "contract_end_invalid": "The last season can't be before this season.",
    "contract_season_missing": "That season isn't set up in the league yet, so the contract can't run that long.",
    "member_is_you": "You can't take yourself off your own team here.",
    "member_not_found": "That person isn't on a team anymore.",
    "player already has canonical active ownership": "That player is already on a team.",
    "cutover": "Contracts are locked while the season rollover runs.",
}

# The rules the commissioner edits in the app: (key, label, where it's saved, kind). Same fields as the website form.
RULE_FIELDS: list[tuple[str, str, str, str]] = [
    ("salary_cap", "Salary cap", "rules", "money"),
    ("max_contract_years", "Longest contract (years)", "rules", "int"),
    ("default_dead_cap_pct", "Dead cap on drops (%)", "rules", "pct"),
    ("one_dollar_deals_no_dead_cap", "$1 deals carry no dead cap", "ai", "bool"),
    ("roster_max", "Roster size (with IR and taxi)", "ai", "int"),
    ("ir_limit", "IR spots", "ai", "int"),
    ("taxi_limit", "Taxi spots", "ai", "int"),
    ("taxi_rookies_only", "Taxi is rookies only", "ai", "bool"),
    ("taxi_locked_full_season", "Taxi player sits the whole season", "ai", "bool"),
    ("taxi_skips_contract_year", "Taxi year doesn't use a contract year", "ai", "bool"),
    ("faab_zero_counts_as", "A $0 waiver bid signs at ($)", "ai", "money"),
    ("faab_pickup_years", "Waiver pickups last (seasons)", "ai", "int"),
    ("min_2_year_bid", "Auction minimum, 2 years ($)", "rules", "money"),
    ("min_3_year_bid", "Auction minimum, 3 years ($)", "rules", "money"),
    ("min_4_year_bid", "Auction minimum, 4 years ($)", "rules", "money"),
    ("rookie_contract_years", "Rookie contract (years)", "rules", "int"),
    ("rookie_option_years", "Rookie team option (years)", "rules", "int"),
    ("win_points", "Points for a win", "ai", "int"),
    ("top_scorer_bonus_points", "Top-scorer bonus points", "ai", "int"),
    ("top_scorer_bonus_count", "Bonus goes to the top", "ai", "int"),
    ("trade_deadline", "Trade deadline (e.g. week 11)", "ai", "text"),
    ("max_trade_teams", "Teams in one trade (max)", "ai", "int"),
    ("playoff_teams", "Playoff teams", "ai", "int"),
    ("playoff_week_start", "First playoff week", "ai", "int"),
    ("playoff_round_weeks", "Weeks per playoff round", "ai", "int"),
]


def tool_error(exc: Exception) -> str:
    text = f"{exc} {getattr(getattr(exc, 'response', None), 'text', '')}"
    for code, message in {**TOOL_ERRORS, **ERRORS}.items():
        if code in text:
            return message
    return "That didn't save. Try again."


def _rpc(client: Any, fn: str, params: Mapping[str, Any]) -> Any:
    try:
        return client.rpc(fn, dict(params)).execute().data
    except Exception as exc:
        raise ValueError(tool_error(exc)) from None


def overview(client: Any, league_id: str) -> dict[str, Any]:
    """Teams with their owners and waiting invites, plus the league switch."""
    from api import profiles, teams
    people = _rpc(client, "commissioner_league_people_authenticated", {"p_league_id": league_id}) or []
    now = datetime.now(timezone.utc).isoformat()
    invites = (client.table("league_invites").select("id,league_team_id,email,role,expires_at").eq("league_id", league_id)
               .eq("status", "pending").gt("expires_at", now).execute().data or [])
    mine = profiles.by_team(client, league_id)
    out = []
    for t in teams.league_teams(client, league_id):
        row = profiles.overlay_team(dict(t), mine.get(t["team_id"]))
        row["people"] = [{"user_id": p["user_id"], "email": p.get("email"), "role": p.get("role")}
                         for p in people if str(p.get("league_team_id")) == t["team_id"]]
        row["invites"] = [{"id": i["id"], "email": i["email"], "role": i["role"]} for i in invites if str(i.get("league_team_id")) == t["team_id"]]
        out.append(row)
    switch = (client.table("league_app_transactions").select("enabled,own_from_week").eq("league_id", league_id).limit(1).execute().data or [{}])[0]
    return {"teams": out, "switch": {"enabled": bool(switch.get("enabled")), "own_from_week": switch.get("own_from_week")}}


def add_player(client: Any, *, league_id: str, season: int, team_id: str, player_id: str, salary: float, years: int, notes: str) -> None:
    from services.offseason_transactions import OffseasonTransactionService
    try:
        OffseasonTransactionService(client, league_id).acquire(
            player_id=player_id, league_team_id=team_id, season=season, salary=salary, years=years,
            acquisition_type="commissioner_manual_add", idempotency_key=f"manual-add:{league_id}:{season}:{player_id}:{int(datetime.now().timestamp())}",
            notes=notes)
    except Exception as exc:
        raise ValueError(tool_error(exc)) from None


def drop_preview(client: Any, league_id: str, player_id: str) -> dict[str, Any]:
    from services.offseason_transactions import OffseasonTransactionService
    try:
        r = OffseasonTransactionService(client, league_id).resolve_manual_drop(player_id)
    except Exception as exc:
        raise ValueError(str(exc) if isinstance(exc, ValueError) else tool_error(exc)) from None
    return {"player_id": r.player_id, "team_name": r.team_name, "salary": float(r.salary_basis), "years_left": r.years_remaining,
            "dead_cap": float(r.dead_cap), "dead_cap_pct": float(r.dead_cap_percentage)}


def drop_player(client: Any, league_id: str, player_id: str, notes: str) -> None:
    from services.offseason_transactions import OffseasonTransactionService
    try:
        OffseasonTransactionService(client, league_id).release_manual_drop(player_id=player_id, notes=notes)
    except Exception as exc:
        raise ValueError(tool_error(exc)) from None


def contract_for(client: Any, league_id: str, player_id: str) -> dict[str, Any]:
    """The player's live contract: id, team, salary by season, last season."""
    rows = (client.table("contract_agreements").select("id,league_team_id,start_season,end_season,status")
            .eq("league_id", league_id).eq("player_id", player_id).in_("status", ["active", "scheduled"])
            .is_("superseded_by_contract_id", "null").execute().data or [])
    if not rows:
        raise ValueError("That player doesn't have a contract.")
    c = rows[0]
    seasons = (client.table("contract_seasons").select("season,salary,obligation_status").eq("contract_id", c["id"])
               .order("season").execute().data or [])
    live = [s for s in seasons if s.get("obligation_status") in ("active", "scheduled")]
    return {"contract_id": c["id"], "team_id": c["league_team_id"], "end_season": c["end_season"],
            "seasons": [{"season": s["season"], "salary": float(s["salary"])} for s in live]}


def fix_contract(client: Any, contract_id: str, salary: float | None, end_season: int | None, notes: str) -> Any:
    return _rpc(client, "commissioner_fix_contract_authenticated", {"p_request": {
        "contract_id": contract_id, "salary": salary, "end_season": end_season, "notes": notes}})


def remove_member(client: Any, league_id: str, user_id: str) -> None:
    _rpc(client, "commissioner_remove_member_authenticated", {"p_league_id": league_id, "p_user_id": user_id})


def set_switch(client: Any, league_id: str, enabled: bool, own_from_week: int | None) -> Any:
    return _rpc(client, "commissioner_set_app_transactions_authenticated",
                {"p_league_id": league_id, "p_enabled": enabled, "p_own_from_week": own_from_week})


def rules_form(client: Any, league_id: str) -> dict[str, Any]:
    from league_ai.settings_store import load_settings
    from scoring.rules import rows_from_rules
    rules = (client.table("league_rules").select("*").eq("league_id", league_id).limit(1).execute().data or [{}])[0]
    ai = load_settings(client, league_id)
    if not ai.scoring_settings:
        ai = _copy_sleeper_rules_once(client, league_id, ai)
    fields = []
    for key, label, where, kind in RULE_FIELDS:
        value = rules.get(key) if where == "rules" else getattr(ai, key)
        fields.append({"key": key, "label": label, "kind": kind, "value": value})
    scoring = rows_from_rules(ai.scoring_settings)
    return {"fields": fields, "scoring": scoring}


def _copy_sleeper_rules_once(client: Any, league_id: str, ai: Any) -> Any:
    """No points per stat saved yet: copy today's Sleeper rules in once and save them as ours, so this form, the
    scoring engine and the GM all read the same saved rules. Only empty settings are filled; nothing is overwritten."""
    from api import league
    from league_ai.settings_store import fetch_sleeper_scoring, save_settings
    try:
        payload = fetch_sleeper_scoring(league.sleeper_league_id(client, league_id))
    except Exception:
        return ai
    if not payload.get("scoring_settings"):
        return ai
    ai.scoring_settings = {k: v for k, v in payload["scoring_settings"].items() if v}
    ai.scoring_source = "sleeper"
    ai.scoring_synced_at = datetime.now(timezone.utc).isoformat()
    if not ai.roster_positions and payload.get("roster_positions"):
        ai.roster_positions = list(payload["roster_positions"])
    deadline = (payload.get("league_settings") or {}).get("trade_deadline")
    if not ai.trade_deadline and deadline not in (None, "", 0, 99):
        ai.trade_deadline = f"week {deadline}"
    try:
        save_settings(client, ai)
    except Exception:
        pass  # still shown; saved on the commissioner's next Save
    return ai


def save_rules(client: Any, league_id: str, values: Mapping[str, Any], scoring: list[Mapping[str, Any]] | None) -> None:
    from league_ai.settings_store import load_settings, save_settings
    from scoring.rules import rules_from_rows
    kinds = {key: (where, kind) for key, _, where, kind in RULE_FIELDS}
    rules_row: dict[str, Any] = {}
    ai = load_settings(client, league_id)
    for key, raw in values.items():
        if key not in kinds:
            continue
        where, kind = kinds[key]
        if kind == "bool":
            value: Any = bool(raw)
        elif kind == "text":
            value = str(raw or "").strip()[:40]
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{key.replace('_', ' ')} has to be a number.") from None
            if value < 0:
                raise ValueError("Numbers can't be negative.")
            if kind == "int":
                value = int(value)
        if where == "rules":
            rules_row[key] = value
        else:
            setattr(ai, key, value)
    new_scoring = rules_from_rows(scoring) if scoring is not None else {}
    if new_scoring:  # an all-blank list never wipes the league's scoring
        ai.scoring_settings = new_scoring
        ai.scoring_source = "app"
    try:
        if rules_row:
            client.table("league_rules").update({**rules_row, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("league_id", league_id).execute()
        save_settings(client, ai)
    except Exception as exc:
        raise ValueError(tool_error(exc)) from None
    from api import cache
    cache.forget("saved_scoring", league_id)  # projections and ranks pick up new points per stat right away
