from typing import Dict, Any

from auth import service_client


def resolve_situation_need(player_name: str, season: int | None = None, context_type: str = "rookie") -> Dict[str, Any]:
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

    situation_score = rookie.get("team_need_fit_score")
    team = rookie.get("nfl_team")

    resolved = bool(team and situation_score is not None)

    if resolved:
        sb.table("player_data_need_queue").update({
            "status": "resolved"
        }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "situation_complete").execute()
    else:
        sb.table("player_data_need_queue").update({
            "status": "blocked_source_missing",
            "source_suggestion": "Need confirmed NFL team/depth chart/team context source"
        }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "situation_complete").execute()

    return {
        "player_name": player_name,
        "resolved": resolved,
        "team": team,
        "team_need_fit_score": situation_score,
        "reason": None if resolved else "Missing confirmed team or situation score.",
    }
