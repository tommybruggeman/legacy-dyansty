from __future__ import annotations

from auth import service_client


def rows_for_owner(owner_team_name: str):
    sb = service_client()

    rows = (
        sb.table("player_graph_v2")
        .select("*")
        .eq("owner_team_name", owner_team_name)
        .execute()
        .data
        or []
    )

    source = "player_graph_v2"

    if not rows:
        rows = (
            sb.table("player_universe")
            .select("*")
            .eq("current_owner", owner_team_name)
            .execute()
            .data
            or []
        )
        source = "player_universe"

    for r in rows:
        production = r.get("production") or {}
        contract = r.get("contract") or {}
        dynasty = r.get("dynasty") or {}
        market = r.get("market") or {}

        primary_ppg = float(
            production.get("primary_ppg")
            or r.get("season_ppg")
            or r.get("expected_ppg")
            or 0
        )

        r["_source"] = source
        r["owner_team_name"] = r.get("owner_team_name") or r.get("current_owner")
        r["current_owner"] = r.get("current_owner") or r.get("owner_team_name")
        r["player_name"] = r.get("player_name") or r.get("name")
        r["pos"] = r.get("pos") or r.get("position")

        r["season_ppg"] = primary_ppg
        r["expected_ppg"] = float(r.get("expected_ppg") or primary_ppg or 0)
        r["primary_ppg"] = primary_ppg
        r["production_trend"] = production.get("trend_label") or "UNKNOWN"
        r["production_confidence"] = production.get("production_confidence") or 0
        r["production_source"] = production.get("source") or "unknown"

        r["salary"] = float(r.get("salary") or contract.get("salary") or 0)
        r["years"] = float(r.get("years") or contract.get("years") or 0)

        r["dynasty_asset_score"] = (
            r.get("dynasty_asset_score")
            or dynasty.get("dynasty_asset_score")
            or dynasty.get("asset_score")
            or 0
        )

        r["trade_value_score"] = (
            r.get("trade_value_score")
            or market.get("trade_value_score")
            or market.get("estimated_market_value")
            or r.get("dynasty_asset_score")
            or 0
        )

        r["contract_efficiency_score"] = (
            r.get("contract_efficiency_score")
            or r.get("contract_score")
            or r.get("contract_value_score")
            or r.get("contract_roi_score")
            or 0
        )

    return rows
