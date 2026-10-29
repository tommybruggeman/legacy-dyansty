"""The app's accuracy report card: how the start/sit engine did on every lineup in the league, by position and week,
and for each owner how often the engine would have beaten their own call. No model call.

The weekly learning job runs start_sit_grade on every saved lineup and saves a compact copy here
(league_ai_experiments, experiment 'start_sit_scorecard_v1', no migration). The Front Office report card reads it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

EXPERIMENT = "start_sit_scorecard_v1"
POSITIONS = ("QB", "RB", "WR", "TE")
METHOD = "engine_live"


def _pair(bucket: Mapping[str, Any] | None) -> dict[str, Any] | None:
    eng, own = (bucket or {}).get(METHOD), (bucket or {}).get("owner")
    if not eng or not own:
        return None
    return {"engine": eng["right"], "owners": own["right"], "calls": eng["calls"]}


def build(res: Mapping[str, Any]) -> dict[str, Any]:
    """The grader's output -> the card: latest season by position and week, all seasons overall, each owner's season."""
    seasons = sorted({int(k.split(":")[1]) for k in res if k.startswith("season:") and k.count(":") == 1})
    if not seasons:
        return {}
    season = seasons[-1]
    weeks = sorted((int(k.split(":")[2]), _pair(v)) for k, v in res.items() if k.startswith(f"week:{season}:"))
    owners = {}
    for k, v in res.items():
        if k.startswith("owner:") and k.endswith(f":{season}"):
            eng, own = (v or {}).get(METHOD), (v or {}).get("owner")
            if eng and own:
                owners[k[len("owner:"):-len(f":{season}")]] = {"engine": eng["right"], "you": own["right"], "calls": eng["calls"],
                                                               "points_per_call": round(eng["pts_per_call"] - own["pts_per_call"], 2)}
    return {
        "season": season,
        "overall": _pair(res.get(f"season:{season}")),
        "all_seasons": _pair(res.get("all")),
        "by_position": [{"pos": p, **pair} for p in POSITIONS if (pair := _pair(res.get(f"season:{season}:{p}")))],
        "by_week": [{"week": w, **pair} for w, pair in weeks if pair],
        "owners": owners,
    }


SYSTEM_USER = "00000000-0000-0000-0000-000000000000"   # rows the weekly job writes for the whole league
OWNER_EXPERIMENT = "start_sit_scorecard_owner_v1"       # each owner's own line, saved under their user id (private)


def save(client: Any, league_id: str, card: Mapping[str, Any], now: datetime | None = None) -> int:
    """The league card (no owner lines) for every member, plus each owner's line under their own user id. Returns owner rows saved."""
    now = now or datetime.now(timezone.utc)
    when = now.isoformat()
    shared = {k: v for k, v in card.items() if k != "owners"}
    client.table("league_ai_experiments").insert({"league_id": league_id, "user_id": SYSTEM_USER, "experiment": EXPERIMENT,
                                                  "question": "weekly start/sit scorecard", "result": {**shared, "computed_at": when}, "cost_usd": 0}).execute()
    teams = {str(t.get("owner_name") or "").casefold(): str(t["id"]) for t in
             client.table("league_teams").select("id, owner_name").eq("league_id", league_id).execute().data or []}
    users = {str(m["league_team_id"]): str(m["user_id"]) for m in
             client.table("league_memberships").select("user_id, league_team_id").eq("league_id", league_id).execute().data or [] if m.get("league_team_id")}
    rows = [{"league_id": league_id, "user_id": users[teams[o.casefold()]], "experiment": OWNER_EXPERIMENT, "question": "weekly start/sit scorecard",
             "result": {**line, "season": card.get("season"), "computed_at": when}, "cost_usd": 0}
            for o, line in (card.get("owners") or {}).items() if teams.get(o.casefold()) in users]
    if rows:
        client.table("league_ai_experiments").insert(rows).execute()
    return len(rows)


def _latest(client: Any, league_id: str, experiment: str, user_id: str | None = None) -> dict[str, Any] | None:
    q = client.table("league_ai_experiments").select("result").eq("league_id", league_id).eq("experiment", experiment)
    if user_id:
        q = q.eq("user_id", user_id)
    try:
        rows = q.order("created_at", desc=True).limit(1).execute().data or []
    except Exception:
        return None
    return rows[0].get("result") if rows else None


def load(client: Any, league_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """The league card, with this owner's own line as 'you' when they have one."""
    card = _latest(client, league_id, EXPERIMENT)
    if not card or not card.get("overall"):
        return None
    mine = _latest(client, league_id, OWNER_EXPERIMENT, user_id) if user_id else None
    out = {k: card.get(k) for k in ("season", "overall", "all_seasons", "by_position", "by_week", "computed_at")}
    if mine and mine.get("season") == card.get("season"):
        out["you"] = {k: mine.get(k) for k in ("engine", "you", "calls", "points_per_call")}
    return out


def run(client: Any, *, now: datetime, dry_run: bool = False) -> dict[str, Any]:
    """Every league with a Sleeper id: grade all lineups and save the card (weekly, from the learning job)."""
    from league_ai.start_sit_grade import run_league

    out = {}
    for lg in client.table("leagues").select("id, sleeper_league_id").execute().data or []:
        if not lg.get("sleeper_league_id"):
            continue
        card = build(run_league(client, str(lg["id"]), str(lg["sleeper_league_id"]), variants=False))
        if not card:
            continue
        out[str(lg["id"])] = {"season": card["season"], "overall": card["overall"]}
        if not dry_run:
            out[str(lg["id"])]["owner_lines"] = save(client, str(lg["id"]), card, now)
    return out


def main() -> int:
    """Save the weekly cards now (start/sit scorecard + accepted-trade shares) without waiting for Tuesday. Counts only:
    error text can hold row contents (owner names) and the Actions logs are public."""
    import os

    from supabase import create_client

    from league_ai.learning_job import run_trade_backtests

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    now = datetime.now(timezone.utc)
    failed = 0
    for name, fn in (("scorecards", lambda: run(client, now=now)), ("trade backtests", lambda: run_trade_backtests(client, now=now))):
        try:
            print(f"{name}: {len(fn())} leagues", flush=True)
        except Exception as exc:
            failed += 1
            print(f"{name} failed: {type(exc).__name__}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
