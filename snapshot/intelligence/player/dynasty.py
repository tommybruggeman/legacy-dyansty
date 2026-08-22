from __future__ import annotations

from .common import find_one, pick, num


def build_dynasty(player_name: str, owner_team_name: str | None = None) -> dict:
    universe = find_one("player_universe", player_name, owner_team_name)
    identity = find_one("player_identity_context", player_name)
    asset = find_one("roster_asset_values", player_name, owner_team_name)
    rec = find_one("player_recommendations", player_name, owner_team_name)
    rookie = find_one("rookie_draft_board", player_name)

    return {
        "dynasty_asset_score": num(pick(asset.get("dynasty_asset_score"), rec.get("dynasty_asset_score"), universe.get("dynasty_asset_score"))),
        "asset_value_score": num(pick(asset.get("asset_value_score"), rec.get("asset_value_score"))),
        "engine_player_score": num(pick(asset.get("engine_player_score"), rec.get("engine_player_score"))),
        "future_projection_score": num(universe.get("future_projection_score")),
        "dynasty_window_score": num(pick(asset.get("dynasty_window_score"), rec.get("dynasty_window_score"))),
        "dynasty_risk_score": num(pick(asset.get("dynasty_risk_score"), rec.get("dynasty_risk_score"))),
        "upside_score": num(asset.get("upside_score")),
        "floor_score": num(asset.get("floor_score")),
        "decline_risk_score": num(asset.get("decline_risk_score")),
        "cornerstone_flag": asset.get("cornerstone_flag"),
        "sell_high_flag": asset.get("sell_high_flag"),
        "buy_low_flag": asset.get("buy_low_flag"),
        "win_now_flag": asset.get("win_now_flag"),
        "rebuild_flag": asset.get("rebuild_flag"),
        "rookie_rank": rookie.get("rookie_rank"),
        "rookie_tier": rookie.get("tier"),
        "final_rookie_score": num(rookie.get("final_rookie_score")),
        "prospect_score": num(rookie.get("prospect_score")),
        "rookie_future_score": num(rookie.get("future_score")),
        "team_need_fit_score": num(rookie.get("team_need_fit_score")),
        "_raw": {
            "player_universe": universe,
            "player_identity_context": identity,
            "roster_asset_values": asset,
            "player_recommendations": rec,
            "rookie_draft_board": rookie,
        },
    }
