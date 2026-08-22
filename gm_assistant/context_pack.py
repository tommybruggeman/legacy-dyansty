from __future__ import annotations

from auth import service_client
from snapshot.builders.rookies.rookie_year import get_active_rookie_class_year


def load_rows(table: str, select: str = "*", limit: int = 25):
    sb = service_client()
    return sb.table(table).select(select).limit(limit).execute().data or []


def load_rookie_board(limit: int = 25):
    sb = service_client()
    year = get_active_rookie_class_year()

    return (
        sb.table("rookie_draft_board")
        .select("rookie_class_year,rookie_rank,player_name,pos,nfl_team,prospect_score,final_rookie_score,tier")
        .eq("rookie_class_year", year)
        .order("rookie_rank")
        .limit(limit)
        .execute()
        .data
        or []
    )


def load_prospect_quality(limit: int = 25):
    sb = service_client()
    year = get_active_rookie_class_year()

    return (
        sb.table("player_prospect_context")
        .select("player_name,position,draft_year,prospect_score,risk_notes,upside_notes")
        .eq("draft_year", year)
        .order("prospect_score", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


def build_context_pack(owner_name: str | None = None) -> dict:
    return {
        "active_rookie_class_year": get_active_rookie_class_year(),
        "rookie_board": load_rookie_board(25),
        "prospect_quality": load_prospect_quality(25),
        "team_summary": load_rows(
            "player_identity_context",
            "player_name,pos,nfl_team,role_score,situation_score,market_score,rookie_asset_score",
            25,
        ),
    }
