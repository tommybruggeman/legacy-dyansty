from __future__ import annotations

import re
import requests
from typing import Any, Mapping

SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"


def normalize_name(s: str) -> str:
    """
    Normalizes names for matching:
    - lowercase
    - strip punctuation
    - collapse whitespace
    """
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_sleeper_players() -> dict:
    r = requests.get(SLEEPER_PLAYERS_URL, timeout=30)
    r.raise_for_status()
    return r.json()


def to_row(pid: str, p: dict) -> dict:
    """
    Maps a Sleeper player object into the Supabase `sleeper_players` row schema.
    IMPORTANT: Do NOT include fields that don't exist in Supabase (ex: is_retired).
    """
    full_name = (p.get("full_name") or "").strip()
    if not full_name:
        full_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()

    position = p.get("position")
    team = p.get("team")
    status = p.get("status")

    # Sleeper often provides `active` boolean; fallback to status if missing
    is_active = p.get("active")
    if is_active is None:
        st = (status or "").lower()
        is_active = False if st in {"inactive", "retired"} else True

    return {
        "sleeper_player_id": pid,
        "full_name": full_name,
        "position": position,
        "team": team,
        "status": status,
        "is_active": bool(is_active),
        "search_name": normalize_name(full_name),
    }


def project_sleeper_player_rows(
    players: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[dict[str, Any], ...]:
    rows = []

    for player_id, player in (players or {}).items():
        if not player:
            continue

        row = to_row(str(player_id), dict(player))
        if not row["full_name"].strip():
            continue

        rows.append(row)

    return tuple(rows)


def refresh_sleeper_players(
    sb: Any,
    *,
    players: Mapping[str, Mapping[str, Any]] | None = None,
    chunk_size: int = 500,
) -> int:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    rows = project_sleeper_player_rows(
        players
        if players is not None
        else fetch_sleeper_players()
    )

    if not rows:
        raise ValueError("Sleeper returned no usable NFL players.")

    for start in range(0, len(rows), chunk_size):
        (
            sb.table("sleeper_players")
            .upsert(
                list(rows[start:start + chunk_size]),
                on_conflict="sleeper_player_id",
            )
            .execute()
        )

    return len(rows)
