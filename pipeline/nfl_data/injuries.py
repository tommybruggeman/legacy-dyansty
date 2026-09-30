"""ESPN injury report -> nfl_injuries; nflverse weekly injury reports -> nfl_practice_reports."""
from __future__ import annotations

from io import StringIO
from typing import Any

import pandas as pd

from pipeline.nfl_data.common import normalize_name, now_iso, to_int, to_text
from pipeline.nfl_data.crosswalk import Crosswalk

ESPN_INJURIES_URLS = [
    "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
]
NFLVERSE_INJURIES_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K"}


def _espn_entries(payload: dict[str, Any]):
    """ESPN nests entries per team: {"injuries": [{"displayName": team, "abbreviation": "ARI", "injuries": [...]}]}."""
    for team in payload.get("injuries") or []:
        abbr = to_text(team.get("abbreviation")) or to_text(team.get("displayName"))
        for entry in team.get("injuries") or []:
            yield abbr, entry


def transform_espn(payload: dict[str, Any], crosswalk: Crosswalk, sleeper_by_espn: dict[str, str] | None = None) -> list[dict[str, Any]]:
    stamp = now_iso()
    sleeper_by_espn = sleeper_by_espn or {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for team, e in _espn_entries(payload):
        athlete = e.get("athlete") or {}
        espn_id = to_text(athlete.get("id")) or to_text(e.get("id"))
        name = to_text(athlete.get("displayName"))
        pos = to_text((athlete.get("position") or {}).get("abbreviation"))
        if not espn_id or not name or pos not in KEEP_POSITIONS or espn_id in seen:
            continue
        seen.add(espn_id)
        details = e.get("details") or {}
        sleeper_id = sleeper_by_espn.get(espn_id) or crosswalk.sleeper_for_espn(espn_id) or crosswalk.sleeper_for_name(name, pos, team)
        rec = crosswalk.record_for_sleeper(sleeper_id) if sleeper_id else None
        rows.append(
            {
                "espn_id": espn_id,
                "sleeper_id": sleeper_id,
                "gsis_id": to_text((rec or {}).get("gsis_id")),
                "player_name": name,
                "search_name": normalize_name(name),
                "position": pos,
                "team": team,
                "status": to_text(e.get("status")),
                "injury_type": to_text(details.get("type")),
                "location": to_text(details.get("location")),
                "detail": to_text(details.get("detail")),
                "side": to_text(details.get("side")),
                "return_date": to_text(details.get("returnDate")),
                "fantasy_status": to_text((details.get("fantasyStatus") or {}).get("description")),
                "short_comment": to_text(e.get("shortComment")),
                "long_comment": to_text(e.get("longComment")),
                "reported_at": to_text(e.get("date")),
                "source": "espn",
                "refreshed_at": stamp,
            }
        )
    return rows


def read_csv(text: str) -> pd.DataFrame:
    return pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)


def transform_nflverse(frame: pd.DataFrame, crosswalk: Crosswalk) -> list[dict[str, Any]]:
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    frame = frame.sort_values(by=["report_status"], key=lambda c: c.fillna("").astype(str).eq(""), kind="stable") if "report_status" in frame.columns else frame
    for _, r in frame.iterrows():
        pos = to_text(r.get("position"))
        gsis = to_text(r.get("gsis_id"))
        season, week = to_int(r.get("season")), to_int(r.get("week"))
        if pos not in KEEP_POSITIONS or not gsis or season is None or week is None:
            continue
        if to_text(r.get("season_type")) not in (None, "REG"):
            continue
        rows.append(
            {
                "gsis_id": gsis,
                "season": season,
                "week": week,
                "sleeper_id": crosswalk.sleeper_for_gsis(gsis),
                "player_name": to_text(r.get("full_name")),
                "position": pos,
                "team": to_text(r.get("team")),
                "report_injury": to_text(r.get("report_primary_injury")),
                "report_status": to_text(r.get("report_status")),
                "practice_injury": to_text(r.get("practice_primary_injury")),
                "practice_status": to_text(r.get("practice_status")),
                "source": "nflverse",
                "refreshed_at": stamp,
            }
        )
    return rows
