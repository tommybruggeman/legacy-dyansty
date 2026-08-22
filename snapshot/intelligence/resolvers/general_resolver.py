from typing import Dict, Any

from auth import service_client


def resolve_general_need(player_name: str, season: int | None = None, context_type: str = "rookie") -> Dict[str, Any]:
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    open_rows = (
        sb.table("player_data_need_queue")
        .select("*")
        .eq("player_name", player_name)
        .eq("season", season)
        .eq("context_type", context_type)
        .eq("status", "open")
        .execute()
        .data or []
    )

    source_tasks = {}

    for r in open_rows:
        source = r.get("source_suggestion") or "Manual review"
        source_tasks.setdefault(source, []).append(r.get("need"))

    sb.table("player_data_need_queue").update({
        "status": "blocked_source_missing",
        "source_suggestion": "Covered by grouped resolver/source task"
    }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "general_review").execute()

    return {
        "player_name": player_name,
        "resolved": False,
        "status": "source_tasks_created",
        "source_tasks": source_tasks,
        "next_step": "Build source integrations for the listed task groups.",
    }
