from __future__ import annotations

from .common import find_one, pick, num


def build_opinion(player_name: str, owner_team_name: str | None = None) -> dict:
    asset = find_one("roster_asset_values", player_name, owner_team_name)
    rec = find_one("player_recommendations", player_name, owner_team_name)

    return {
        "recommendation": pick(rec.get("recommendation"), asset.get("asset_recommendation")),
        "asset_recommendation": asset.get("asset_recommendation"),
        "confidence": num(rec.get("confidence")),
        "reasoning": rec.get("reasoning"),
        "career_stage": pick(rec.get("career_stage"), asset.get("career_stage")),
        "engine_tier": asset.get("engine_tier"),
        "_raw": {
            "roster_asset_values": asset,
            "player_recommendations": rec,
        },
    }
