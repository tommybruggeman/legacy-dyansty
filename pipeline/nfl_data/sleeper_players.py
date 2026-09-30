"""Sleeper players -> nfl_players rows."""
from __future__ import annotations

from typing import Any

from pipeline.nfl_data.common import normalize_name, now_iso, to_float, to_int, to_text
from pipeline.nfl_data.crosswalk import Crosswalk

SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def transform_players(players: dict[str, dict[str, Any]], crosswalk: Crosswalk | None = None) -> list[dict[str, Any]]:
    """Keep fantasy-relevant players; enrich with crosswalk IDs and draft data."""
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    for sleeper_id, p in players.items():
        if not isinstance(p, dict):
            continue
        pos = to_text(p.get("position"))
        if pos not in KEEP_POSITIONS:
            continue
        full_name = to_text(p.get("full_name")) or " ".join(x for x in [to_text(p.get("first_name")), to_text(p.get("last_name"))] if x)
        if not full_name:
            continue
        active = bool(p.get("active"))
        status = to_text(p.get("status"))
        if not active and status not in {"Active", "Injured Reserve", "PUP", "Suspended"} and pos != "DEF":
            # retired / inactive players stay out unless a team still lists them
            if not to_text(p.get("team")):
                continue
        rec = crosswalk.record_for_sleeper(sleeper_id) if crosswalk else None
        rec = rec or {}
        row = {
            "sleeper_id": str(sleeper_id),
            "full_name": full_name,
            "first_name": to_text(p.get("first_name")),
            "last_name": to_text(p.get("last_name")),
            "search_name": normalize_name(full_name),
            "position": pos,
            "team": to_text(p.get("team")),
            "age": to_float(p.get("age")),
            "birthdate": to_text(p.get("birth_date")) or to_text(rec.get("birthdate")),
            "years_exp": to_int(p.get("years_exp")),
            "depth_chart_order": to_int(p.get("depth_chart_order")),
            "depth_chart_position": to_text(p.get("depth_chart_position")),
            "injury_status": to_text(p.get("injury_status")),
            "injury_body_part": to_text(p.get("injury_body_part")),
            "injury_notes": to_text(p.get("injury_notes")),
            "status": status,
            "active": active,
            "college": to_text(p.get("college")) or to_text(rec.get("college")),
            "draft_year": to_int(rec.get("draft_year")),
            "draft_round": to_int(rec.get("draft_round")),
            "draft_pick": to_int(rec.get("draft_pick")),
            "draft_ovr": to_int(rec.get("draft_ovr")),
            "height": to_text(p.get("height")),
            "weight": to_int(p.get("weight")),
            "gsis_id": to_text(rec.get("gsis_id")),
            "fantasypros_id": to_text(rec.get("fantasypros_id")),
            "pfr_id": to_text(rec.get("pfr_id")),
            "cfbref_id": to_text(rec.get("cfbref_id")),
            "ktc_id": to_text(rec.get("ktc_id")),
            "search_rank": to_int(p.get("search_rank")),
            "source": "sleeper",
            "refreshed_at": stamp,
        }
        if row["draft_year"] is None and to_int(p.get("years_exp")) is not None and pos != "DEF":
            # Sleeper has no draft year; fall back to the rookie season implied by experience for age-curve math
            pass
        rows.append(row)
    return rows
