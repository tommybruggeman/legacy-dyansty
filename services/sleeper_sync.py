from __future__ import annotations

import re
import requests

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
