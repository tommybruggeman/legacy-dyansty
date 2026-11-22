"""Prospect Power Rankings for the app and the website: the newest weekly edition plus the owner's starred prospects.

Editions are written by league_ai/prospect_rankings.py (Tuesdays) into prospect_rankings; stars live in prospect_stars,
one row per owner and prospect, readable and writable only by that owner (row level security).
"""
from __future__ import annotations

from typing import Any

from api import cache


def latest(client: Any) -> dict[str, Any] | None:
    """The newest edition, cached for 10 minutes (it changes once a week)."""
    def load() -> dict[str, Any] | None:
        try:
            rows = (client.table("prospect_rankings").select("edition_date,season,number,data")
                    .order("edition_date", desc=True).limit(1).execute().data)
        except Exception:
            return None  # table not created yet
        return rows[0] if rows else None
    return cache.cached(("prospect_rankings",), 600, load)


def stars(client: Any, user_id: str) -> list[str]:
    try:
        rows = client.table("prospect_stars").select("athlete_id").eq("user_id", str(user_id)).limit(500).execute().data or []
    except Exception:
        return []
    return [str(r["athlete_id"]) for r in rows]


def set_star(client: Any, user_id: str, athlete_id: str, on: bool) -> list[str]:
    athlete_id = str(athlete_id).strip()
    if not athlete_id or len(athlete_id) > 20:
        raise ValueError("Unknown prospect.")
    if on:
        client.table("prospect_stars").upsert({"user_id": str(user_id), "athlete_id": athlete_id}, on_conflict="user_id,athlete_id").execute()
    else:
        client.table("prospect_stars").delete().eq("user_id", str(user_id)).eq("athlete_id", athlete_id).execute()
    return stars(client, user_id)


def page(client: Any, user_id: str) -> dict[str, Any]:
    """{edition: {...} | None, starred: [athlete ids]} for the Prospect Rankings page and the Home card."""
    ed = latest(client)
    if not ed:
        return {"edition": None, "starred": []}
    data = ed.get("data") or {}
    return {"edition": {"date": ed["edition_date"], "season": ed["season"], "number": ed.get("number") or 1, **data},
            "starred": stars(client, user_id)}
