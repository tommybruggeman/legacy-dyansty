from typing import Dict, Any

from auth import service_client


def resolve_market_need(player_name: str, season: int | None = None, context_type: str = "rookie") -> Dict[str, Any]:
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

    # v1: future_score is a proxy market signal, not true market.
    has_true_market = any([
        rookie.get("adp"),
        rookie.get("trade_value"),
        rookie.get("asset_score"),
        rookie.get("market_score"),
    ])

    has_proxy_market = bool(rookie.get("future_score"))

    if has_true_market:
        status = "resolved"
        resolved = True
        reason = "True market field found."
    elif has_proxy_market:
        status = "blocked_source_missing"
        resolved = False
        reason = "Only proxy market value exists; true ADP/trade market source missing."
    else:
        status = "blocked_source_missing"
        resolved = False
        reason = "No market value source found."

    sb.table("player_data_need_queue").update({
        "status": status,
        "source_suggestion": "Add dynasty ADP / trade value / market feed"
    }).eq("player_name", player_name).eq("season", season).eq("context_type", context_type).eq("need", "market_complete").execute()

    return {
        "player_name": player_name,
        "resolved": resolved,
        "status": status,
        "has_true_market": bool(has_true_market),
        "has_proxy_market": bool(has_proxy_market),
        "reason": reason,
    }
