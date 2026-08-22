from typing import Dict, Any

from auth import service_client


def _has_draft_profile(row: Dict[str, Any]) -> bool:
    fields = [
        row.get("draft_year"),
        row.get("draft_round"),
        row.get("draft_pick"),
        row.get("rookie_year"),
        row.get("prospect_tier"),
        row.get("source"),
    ]
    return any(v not in [None, "", 0] for v in fields)


def resolve_draft_profile_need(player_name: str, season: int | None = None, context_type: str = "rookie") -> Dict[str, Any]:
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    rookie_rows = (
        sb.table("rookie_draft_board")
        .select("*")
        .eq("player_name", player_name)
        .eq("rookie_class_year", season)
        .limit(1)
        .execute()
        .data or []
    )

    if not rookie_rows:
        return {
            "player_name": player_name,
            "resolved": False,
            "reason": "No rookie_draft_board row found.",
        }

    rookie = rookie_rows[0]

    updates = {}

    # Try player_prospect_context as enrichment source if it exists and has rows.
    try:
        prospect_rows = (
            sb.table("player_prospect_context")
            .select("*")
            .ilike("player_name", f"%{player_name}%")
            .limit(5)
            .execute()
            .data or []
        )
    except Exception:
        prospect_rows = []

    best = prospect_rows[0] if prospect_rows else {}

    for field in ["draft_year", "draft_round", "draft_pick", "rookie_year", "prospect_tier", "source"]:
        if not rookie.get(field) and best.get(field):
            updates[field] = best.get(field)

    if updates:
        sb.table("rookie_draft_board").update(updates).eq("player_name", player_name).eq("rookie_class_year", season).execute()

    refreshed_rows = (
        sb.table("rookie_draft_board")
        .select("*")
        .eq("player_name", player_name)
        .eq("rookie_class_year", season)
        .limit(1)
        .execute()
        .data or []
    )

    refreshed = refreshed_rows[0] if refreshed_rows else rookie
    resolved = _has_draft_profile(refreshed)

    if resolved:
        sb.table("player_data_need_queue").update({
            "status": "resolved"
        }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "draft_profile_complete").execute()
    else:
        sb.table("player_data_need_queue").update({
            "status": "blocked_source_missing",
            "source_suggestion": "Add draft capital / rookie class / prospect source data"
        }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "draft_profile_complete").execute()

    return {
        "player_name": player_name,
        "resolved": resolved,
        "updates": updates,
        "prospect_matches": len(prospect_rows),
        "reason": None if resolved else "No usable draft profile source found.",
        "next_step": None if resolved else "Add draft capital / rookie class / prospect source data.",
    }
