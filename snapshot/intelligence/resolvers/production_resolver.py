from typing import Dict, Any

from auth import service_client


def resolve_production_need(player_name: str, season: int | None = None, context_type: str = "rookie") -> Dict[str, Any]:
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    # v1 checks whether any usable production fields already exist.
    rookie_rows = (
        sb.table("rookie_draft_board")
        .select("*")
        .eq("player_name", player_name)
        .eq("rookie_class_year", season)
        .limit(1)
        .execute()
        .data or []
    )

    projection_rows = (
        sb.table("player_projection_context")
        .select("*")
        .eq("player_name", player_name)
        .eq("season", season)
        .limit(1)
        .execute()
        .data or []
    )

    rookie = rookie_rows[0] if rookie_rows else {}
    projection = projection_rows[0] if projection_rows else {}

    has_projection = any([
        projection.get("year_1_projected_points"),
        projection.get("year_2_projected_points"),
        projection.get("year_3_projected_points"),
    ])

    # True production means actual stats, not just projection.
    has_actual_production = any([
        rookie.get("season_ppg"),
        rookie.get("points"),
        rookie.get("games"),
    ])

    if has_actual_production:
        status = "resolved"
        resolved = True
        reason = "Actual production found."
    elif has_projection:
        status = "blocked_source_missing"
        resolved = False
        reason = "Only projection exists; actual college/NFL production source missing."
    else:
        status = "blocked_source_missing"
        resolved = False
        reason = "No production or projection data found."

    sb.table("player_data_need_queue").update({
        "status": status,
        "source_suggestion": "Add college production / NFLverse / Sleeper stats source"
    }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "production_complete").execute()

    return {
        "player_name": player_name,
        "resolved": resolved,
        "status": status,
        "has_projection": bool(has_projection),
        "has_actual_production": bool(has_actual_production),
        "reason": reason,
    }
