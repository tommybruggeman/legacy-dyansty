from __future__ import annotations

from .common import sb


def build_evidence(player_name: str, season: int | None = None) -> dict:
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    client = sb()

    try:
        tasks = (
            client.table("legacy_source_task_queue")
            .select("source_id,source_name,resolver,priority,status,needs")
            .eq("player_name", player_name)
            .eq("season", season)
            .order("priority")
            .execute()
            .data or []
        )
    except Exception as e:
        tasks = [{"_error": str(e)}]

    open_tasks = [t for t in tasks if t.get("status") == "open"]

    confidence = 100
    if open_tasks:
        confidence -= min(60, len(open_tasks) * 7)

    priority_one_open = [t for t in open_tasks if int(t.get("priority") or 99) == 1]
    if priority_one_open:
        confidence -= 15

    confidence = max(10, confidence)

    missing_fields = []
    for t in open_tasks:
        missing_fields.extend(t.get("needs") or [])

    return {
        "confidence": confidence,
        "open_task_count": len(open_tasks),
        "priority_one_open_count": len(priority_one_open),
        "missing_fields": sorted(set(missing_fields)),
        "source_tasks": tasks,
    }
