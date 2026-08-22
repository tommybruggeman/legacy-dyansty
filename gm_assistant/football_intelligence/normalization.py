from __future__ import annotations

from typing import Any

from gm_assistant.repositories.common import clean_id, safe_float, safe_int


PLAYER_POSITION_ALIASES = {
    "QB": "QB",
    "QUARTERBACK": "QB",
    "RB": "RB",
    "RUNNING BACK": "RB",
    "RUNNINGBACK": "RB",
    "WR": "WR",
    "WIDE RECEIVER": "WR",
    "RECEIVER": "WR",
    "TE": "TE",
    "TIGHT END": "TE",
    "K": "K",
    "PK": "K",
    "KICKER": "K",
    "DEF": "DST",
    "DST": "DST",
    "D/ST": "DST",
    "DEFENSE": "DST",
    "DL": "DL",
    "DE": "DL",
    "DT": "DL",
    "LB": "LB",
    "DB": "DB",
    "CB": "DB",
    "S": "DB",
    "IDP": "IDP",
}

LINEUP_SLOT_ALIASES = {
    **PLAYER_POSITION_ALIASES,
    "FLEX": "FLEX",
    "RB/WR/TE": "FLEX",
    "WR/RB/TE": "FLEX",
    "REC_FLEX": "FLEX",
    "SUPERFLEX": "SUPERFLEX",
    "SUPER FLEX": "SUPERFLEX",
    "SF": "SUPERFLEX",
    "OP": "SUPERFLEX",
    "OFFENSIVE PLAYER": "SUPERFLEX",
}

FLEX_ELIGIBILITY = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPERFLEX": ("QB", "RB", "WR", "TE"),
    "IDP": ("DL", "LB", "DB", "IDP"),
}


def normalize_player_position(value: Any) -> str | None:
    text = _norm(value)
    if not text:
        return None
    return PLAYER_POSITION_ALIASES.get(text)


def normalize_lineup_slot(value: Any) -> str | None:
    text = _norm(value)
    if not text:
        return None
    return LINEUP_SLOT_ALIASES.get(text)


def eligible_positions_for_slot(slot: str) -> tuple[str, ...]:
    slot = slot.upper()
    return FLEX_ELIGIBILITY.get(slot, (slot,))


def normalize_roster_status(row: dict[str, Any]) -> str:
    text = str(row.get("status") or row.get("roster_status") or row.get("roster_designation") or "active").strip().lower()
    if text in {"taxi", "taxi_squad", "taxi squad"}:
        return "taxi"
    if text in {"ir", "injured_reserve", "injured reserve"}:
        return "ir"
    if text in {"released", "release", "cut", "dropped", "waived", "inactive_released"}:
        return "released"
    return "active"


def row_player_id(row: dict[str, Any]) -> str | None:
    return clean_id(row.get("sleeper_id") or row.get("player_id") or row.get("sleeper_player_id"))


def row_player_name(row: dict[str, Any]) -> str | None:
    return clean_id(row.get("player_name") or row.get("name") or row.get("full_name"))


def row_position(row: dict[str, Any]) -> str | None:
    return normalize_player_position(row.get("position") or row.get("player_position") or row.get("pos"))


def row_salary(row: dict[str, Any]) -> float | None:
    return safe_float(row.get("salary") or row.get("cap_hit") or row.get("contract_salary"))


def row_contract_years(row: dict[str, Any]) -> int | None:
    return safe_int(row.get("contract_years_left") or row.get("years_remaining") or row.get("contract_years_remaining"))


def row_age(row: dict[str, Any]) -> float | None:
    return safe_float(row.get("age"))


def row_experience(row: dict[str, Any]) -> int | None:
    return safe_int(row.get("experience") or row.get("years_exp"))


def row_is_rookie(row: dict[str, Any]) -> bool | None:
    value = row.get("is_rookie")
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "t", "yes", "y", "1", "rookie"}:
        return True
    if text in {"false", "f", "no", "n", "0", "veteran"}:
        return False
    exp = row_experience(row)
    if exp == 0:
        return True
    return None


def _norm(value: Any) -> str:
    return str(value or "").replace("-", " ").replace("_", " ").strip().upper()
