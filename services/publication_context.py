"""Read-only access to the certified published application context."""
from __future__ import annotations

from typing import Any


def published_context(sb: Any, league_id: str) -> dict:
    rows = (
        sb.table("publication_context_generations")
        .select("*")
        .eq("league_id", str(league_id))
        .eq("publication_context_status", "published")
        .order("context_generation", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    return dict(rows[0]) if rows else {}


def publication_generation(sb: Any, league_id: str) -> int:
    return int(published_context(sb, league_id).get("context_generation") or 0)


def published_cap_rows(sb: Any, league_id: str) -> list[dict]:
    context = published_context(sb, league_id)
    cap_publication_id = context.get("cap_publication_id")
    if not cap_publication_id:
        return []
    publications = (
        sb.table("rollover_target_cap_authority_publications")
        .select("prepared_cap_set_id")
        .eq("id", cap_publication_id)
        .eq("league_id", league_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not publications:
        return []
    return [
        dict(row)
        for row in (
            sb.table("prepared_team_caps")
            .select("*")
            .eq("cap_set_id", publications[0]["prepared_cap_set_id"])
            .eq("league_id", league_id)
            .execute()
            .data
            or []
        )
    ]


def published_market_player_ids(sb: Any, league_id: str, market_type: str) -> frozenset[str]:
    try:
        context = published_context(sb, league_id)
    except Exception:
        return frozenset()
    publication_id = context.get("market_publication_id")
    if not publication_id:
        return frozenset()
    rows = (
        sb.table("rollover_target_market_visibility_rows")
        .select("player_id")
        .eq("publication_id", publication_id)
        .eq("league_id", league_id)
        .eq("market_type", market_type)
        .eq("visibility_status", "visible")
        .execute()
        .data
        or []
    )
    return frozenset(str(row.get("player_id") or "").strip() for row in rows if row.get("player_id"))
