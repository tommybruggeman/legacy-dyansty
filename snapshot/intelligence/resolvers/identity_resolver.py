from typing import Dict, Any

from auth import service_client


def resolve_identity_need(player_name: str, season: int | None = None, context_type: str = "rookie") -> Dict[str, Any]:
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    universe_rows = (
        sb.table("player_universe")
        .select("*")
        .ilike("player_name", f"%{player_name}%")
        .limit(5)
        .execute()
        .data or []
    )

    if not universe_rows:
        sb.table("player_data_need_queue").update({
            "status": "blocked_source_missing",
            "source_suggestion": "Add player to player_universe or improve prospect identity source"
        }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "identity_complete").execute()

        return {
            "player_name": player_name,
            "resolved": False,
            "reason": "No player_universe match found.",
            "next_step": "Add player to player_universe or improve prospect identity source.",
            "matches": [],
        }

    best = universe_rows[0]

    updates = {}

    for field in ["sleeper_id", "gsis_id", "pos", "nfl_team"]:
        if best.get(field):
            updates[field] = best.get(field)

    if updates:
        sb.table("rookie_draft_board").update(updates).eq("player_name", player_name).eq("rookie_class_year", season).execute()

        sb.table("player_data_need_queue").update({
            "status": "resolved"
        }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "identity_complete").execute()

    return {
        "player_name": player_name,
        "resolved": bool(updates),
        "updates": updates,
        "match_count": len(universe_rows),
        "best_match": {
            "player_name": best.get("player_name"),
            "sleeper_id": best.get("sleeper_id"),
            "gsis_id": best.get("gsis_id"),
            "pos": best.get("pos"),
            "nfl_team": best.get("nfl_team"),
        },
    }
