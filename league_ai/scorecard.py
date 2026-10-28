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


def save(client: Any, league_id: str, card: Mapping[str, Any], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    client.table("league_ai_experiments").insert({"league_id": league_id, "experiment": EXPERIMENT, "question": "weekly start/sit scorecard",
                                                  "result": {**card, "computed_at": now.isoformat()}, "cost_usd": 0}).execute()


def load(client: Any, league_id: str) -> dict[str, Any] | None:
    try:
        rows = (client.table("league_ai_experiments").select("result").eq("league_id", league_id).eq("experiment", EXPERIMENT)
                .order("created_at", desc=True).limit(1).execute().data or [])
    except Exception:
        return None
    return rows[0].get("result") if rows else None


def for_owner(card: Mapping[str, Any] | None, owner_name: str) -> dict[str, Any] | None:
    """The app's view: league numbers plus this owner's own line (others' lines stay private)."""
    if not card or not card.get("overall"):
        return None
    mine = next((v for k, v in (card.get("owners") or {}).items() if k.casefold() == (owner_name or "").casefold()), None)
    return {k: card.get(k) for k in ("season", "overall", "all_seasons", "by_position", "by_week", "computed_at")} | ({"you": mine} if mine else {})


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
            save(client, str(lg["id"]), card, now)
    return out
