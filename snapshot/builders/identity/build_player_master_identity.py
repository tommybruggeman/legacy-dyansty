from __future__ import annotations

from typing import Any, Dict, List
import requests

from auth import service_client


SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"


def norm_name(name: Any) -> str:
    return " ".join(str(name or "").lower().replace(".", "").replace("'", "").split())


def n(*vals, default=None):
    for v in vals:
        try:
            if v is not None and v != "":
                return float(v)
        except Exception:
            pass
    return default


def i(*vals, default=None):
    val = n(*vals, default=None)
    return int(val) if val is not None else default


def text(*vals):
    for v in vals:
        if v not in [None, ""]:
            return v
    return None


def load_sleeper_players() -> Dict[str, Dict[str, Any]]:
    resp = requests.get(SLEEPER_PLAYERS_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def build_row(sleeper_id: str, sp: Dict[str, Any], universe_row: Dict[str, Any] | None = None) -> Dict[str, Any]:
    universe_row = universe_row or {}

    first = sp.get("first_name")
    last = sp.get("last_name")
    full_name = text(
        sp.get("full_name"),
        sp.get("search_full_name"),
        f"{first} {last}".strip() if first or last else None,
        universe_row.get("player_name"),
    )

    birth_date = sp.get("birth_date")
    age = n(sp.get("age"), universe_row.get("age"), default=None)

    years_exp = i(sp.get("years_exp"), universe_row.get("years_exp"))
    draft_year = i(sp.get("draft_year"), universe_row.get("draft_year"))

    return {
        "sleeper_id": str(sleeper_id),
        "gsis_id": text(sp.get("gsis_id"), universe_row.get("gsis_id")),
        "canonical_player_id": text(universe_row.get("canonical_player_id"), str(sleeper_id)),
        "player_name": full_name,
        "search_name": norm_name(full_name),
        "pos": text(sp.get("position"), universe_row.get("pos")),
        "nfl_team": text(sp.get("team"), universe_row.get("nfl_team")),
        "age": age,
        "age_estimated": False,
        "birth_date": birth_date,
        "years_exp": years_exp,
        "draft_year": draft_year,
        "draft_round": i(sp.get("draft_round"), universe_row.get("draft_round")),
        "draft_pick": i(sp.get("draft_number"), sp.get("draft_pick"), universe_row.get("draft_pick")),
        "college": text(sp.get("college"), universe_row.get("college")),
        "active": sp.get("active"),
        "nfl_status": text(sp.get("status"), universe_row.get("nfl_status")),
        "identity_source": "sleeper_players_nfl",
    }


def build_player_master_identity():
    sb = service_client()

    universe = sb.table("player_universe").select("*").execute().data or []
    universe_by_sid = {
        str(r.get("sleeper_id")): r
        for r in universe
        if r.get("sleeper_id")
    }

    sleeper_players = load_sleeper_players()

    rows: List[Dict[str, Any]] = []

    # Build master identity for every player currently in universe.
    # This keeps table size controlled while using Sleeper as identity authority.
    for sleeper_id, u in universe_by_sid.items():
        sp = sleeper_players.get(str(sleeper_id), {}) or {}
        row = build_row(sleeper_id, sp, u)

        if not row["player_name"] or not row["pos"]:
            continue

        rows.append(row)

    if rows:
        sb.table("player_master_identity").upsert(rows, on_conflict="sleeper_id").execute()

    print(f"Upserted {len(rows)} player_master_identity rows from Sleeper identity")


if __name__ == "__main__":
    build_player_master_identity()
