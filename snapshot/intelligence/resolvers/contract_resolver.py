from typing import Dict, Any

from auth import service_client


def resolve_contract_need(player_name: str, season: int | None = None, context_type: str = "rookie") -> Dict[str, Any]:
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

    has_contract = any([
        rookie.get("salary"),
        rookie.get("years"),
        rookie.get("has_contract"),
        rookie.get("current_owner"),
        rookie.get("owner_team_name"),
    ])

    if has_contract:
        status = "resolved"
        resolved = True
        reason = "Contract context found."
    else:
        status = "blocked_source_missing"
        resolved = False
        reason = "No Legacy contract/owner context found."

    sb.table("player_data_need_queue").update({
        "status": status,
        "source_suggestion": "Add Legacy contract ownership/salary/years context"
    }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "contract_complete").execute()

    return {
        "player_name": player_name,
        "resolved": resolved,
        "status": status,
        "reason": reason,
    }
