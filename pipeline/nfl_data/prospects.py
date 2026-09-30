"""CollegeFootballData -> nfl_prospects: production, usage, class, recruiting pedigree, NFL draft result."""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from pipeline.nfl_data.common import normalize_name, now_iso, to_float, to_int, to_text
from pipeline.nfl_data.crosswalk import Crosswalk

CFBD_BASE = "https://api.collegefootballdata.com"
KEEP_POSITIONS = {"QB", "RB", "WR", "TE"}
STAT_MAP = {
    ("passing", "ATT"): "pass_att", ("passing", "YDS"): "pass_yds", ("passing", "TD"): "pass_td", ("passing", "INT"): "pass_int",
    ("rushing", "CAR"): "rush_att", ("rushing", "ATT"): "rush_att", ("rushing", "YDS"): "rush_yds", ("rushing", "TD"): "rush_td",
    ("receiving", "REC"): "receptions", ("receiving", "YDS"): "rec_yds", ("receiving", "TD"): "rec_td",
}


def cfbd_headers() -> dict[str, str]:
    key = os.environ.get("CFBD_API_KEY", "").strip()
    if not key:
        raise RuntimeError("CFBD_API_KEY is not set")
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def fetch_cfbd(path: str, **params: Any) -> Any:
    import requests

    resp = requests.get(f"{CFBD_BASE}{path}", params={k: v for k, v in params.items() if v is not None}, headers=cfbd_headers(), timeout=120)
    resp.raise_for_status()
    return resp.json()


def _g(obj: Any, *names: str, default: Any = None) -> Any:
    """First present key among alternatives (CFBD has renamed fields across versions)."""
    for n in names:
        if isinstance(obj, dict) and obj.get(n) is not None:
            return obj[n]
    return default


def build_prospects(
    season: int,
    *,
    season_stats: list[dict[str, Any]],
    usage: list[dict[str, Any]],
    roster: list[dict[str, Any]],
    recruits: list[dict[str, Any]],
    draft_picks: list[dict[str, Any]],
    crosswalk: Crosswalk | None = None,
) -> list[dict[str, Any]]:
    stamp = now_iso()
    players: dict[str, dict[str, Any]] = defaultdict(dict)

    for r in roster:
        pid = to_text(_g(r, "id", "athleteId"))
        pos = to_text(_g(r, "position"))
        if not pid or pos not in KEEP_POSITIONS:
            continue
        p = players[pid]
        p.update({
            "name": " ".join(x for x in [to_text(_g(r, "firstName", "first_name")), to_text(_g(r, "lastName", "last_name"))] if x) or to_text(_g(r, "name")),
            "position": pos, "college": to_text(_g(r, "team")), "class_year": to_int(_g(r, "year")),
            "height": to_float(_g(r, "height")), "weight": to_float(_g(r, "weight")),
            "recruit_ids": _g(r, "recruitIds", "recruit_ids", default=[]) or [],
        })

    for r in usage:
        pid = to_text(_g(r, "id", "athleteId", "playerId"))
        pos = to_text(_g(r, "position"))
        if not pid or pos not in KEEP_POSITIONS:
            continue
        u = _g(r, "usage", default={}) or {}
        p = players[pid]
        p.setdefault("name", to_text(_g(r, "name", "player")))
        p.setdefault("position", pos)
        p.setdefault("college", to_text(_g(r, "team")))
        p["conference"] = to_text(_g(r, "conference"))
        p["usage_overall"] = to_float(_g(u, "overall"))
        p["usage_pass"] = to_float(_g(u, "pass"))
        p["usage_rush"] = to_float(_g(u, "rush"))

    for r in season_stats:
        pid = to_text(_g(r, "playerId", "athleteId", "id"))
        pos = to_text(_g(r, "position"))
        if not pid or pos not in KEEP_POSITIONS:
            continue
        key = (str(_g(r, "category", default="")).lower(), str(_g(r, "statType", "stat_type", default="")).upper())
        col = STAT_MAP.get(key)
        if not col:
            continue
        p = players[pid]
        p.setdefault("name", to_text(_g(r, "player", "name")))
        p.setdefault("position", pos)
        p.setdefault("college", to_text(_g(r, "team")))
        p.setdefault("conference", to_text(_g(r, "conference")))
        p[col] = to_float(_g(r, "stat", "value"))

    recruit_by_athlete: dict[str, dict[str, Any]] = {}
    recruit_by_name: dict[str, dict[str, Any]] = {}
    for r in recruits:
        rec = {"stars": to_int(_g(r, "stars")), "rating": to_float(_g(r, "rating")), "rank": to_int(_g(r, "ranking")), "year": to_int(_g(r, "year"))}
        aid = to_text(_g(r, "athleteId", "athlete_id"))
        if aid:
            recruit_by_athlete[aid] = rec
        rid = to_text(_g(r, "id"))
        if rid:
            recruit_by_athlete.setdefault("recruit:" + rid, rec)
        nm = normalize_name(_g(r, "name"))
        if nm:
            recruit_by_name.setdefault(nm, rec)

    drafted: dict[str, dict[str, Any]] = {}
    for r in draft_picks:
        aid = to_text(_g(r, "collegeAthleteId", "college_athlete_id"))
        rec = {"year": to_int(_g(r, "year")), "round": to_int(_g(r, "round")), "pick": to_int(_g(r, "overall", "pick")), "team": to_text(_g(r, "nflTeam", "nfl_team"))}
        if aid:
            drafted[aid] = rec
        nm = normalize_name(_g(r, "name"))
        if nm:
            drafted.setdefault("name:" + nm, rec)

    rows: list[dict[str, Any]] = []
    for pid, p in players.items():
        name = p.get("name")
        if not name or not p.get("position"):
            continue
        has_production = any(p.get(k) for k in ("rush_yds", "rec_yds", "pass_yds", "usage_overall"))
        if not has_production:
            continue
        rec = recruit_by_athlete.get(pid)
        if rec is None:
            for rid in p.get("recruit_ids") or []:
                rec = recruit_by_athlete.get("recruit:" + str(rid))
                if rec:
                    break
        rec = rec or recruit_by_name.get(normalize_name(name)) or {}
        d = drafted.get(pid) or drafted.get("name:" + normalize_name(name)) or {}
        class_year = p.get("class_year")
        rows.append({
            "cfbd_athlete_id": pid,
            "season": season,
            "name": name,
            "search_name": normalize_name(name),
            "position": p.get("position"),
            "college": p.get("college"),
            "conference": p.get("conference"),
            "class_year": class_year,
            "draft_eligible": (class_year >= 3) if class_year is not None else None,
            "height": p.get("height"),
            "weight": p.get("weight"),
            "recruit_stars": rec.get("stars"),
            "recruit_rating": rec.get("rating"),
            "recruit_rank": rec.get("rank"),
            "recruit_year": rec.get("year"),
            "usage_overall": p.get("usage_overall"),
            "usage_pass": p.get("usage_pass"),
            "usage_rush": p.get("usage_rush"),
            "pass_att": p.get("pass_att"), "pass_yds": p.get("pass_yds"), "pass_td": p.get("pass_td"), "pass_int": p.get("pass_int"),
            "rush_att": p.get("rush_att"), "rush_yds": p.get("rush_yds"), "rush_td": p.get("rush_td"),
            "receptions": p.get("receptions"), "rec_yds": p.get("rec_yds"), "rec_td": p.get("rec_td"),
            "scrimmage_yds": (p.get("rush_yds") or 0) + (p.get("rec_yds") or 0),
            "nfl_draft_year": d.get("year"), "nfl_draft_round": d.get("round"), "nfl_draft_pick": d.get("pick"), "nfl_team": d.get("team"),
            "sleeper_id": crosswalk.sleeper_for_name(name, p.get("position")) if (crosswalk and d) else None,
            "source": "cfbd",
            "refreshed_at": stamp,
        })
    return rows


def fetch_season_inputs(season: int, recruit_years: list[int]) -> dict[str, Any]:
    stats: list[dict[str, Any]] = []
    for category in ("passing", "rushing", "receiving"):
        stats.extend(fetch_cfbd("/stats/player/season", year=season, seasonType="regular", category=category) or [])
    usage = fetch_cfbd("/player/usage", year=season, excludeGarbageTime="true") or []
    roster = fetch_cfbd("/roster", year=season) or []
    recruits: list[dict[str, Any]] = []
    for y in recruit_years:
        try:
            recruits.extend(fetch_cfbd("/recruiting/players", year=y) or [])
        except Exception as exc:
            print(f"[nfl_data] recruiting {y} unavailable ({type(exc).__name__})", flush=True)
    picks: list[dict[str, Any]] = []
    for y in (season + 1, season + 2):
        try:
            picks.extend(fetch_cfbd("/draft/picks", year=y) or [])
        except Exception:
            pass
    return {"season_stats": stats, "usage": usage, "roster": roster, "recruits": recruits, "draft_picks": picks}
