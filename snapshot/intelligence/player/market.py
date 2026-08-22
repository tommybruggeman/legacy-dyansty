from __future__ import annotations

from .common import find_one, pick, num


def build_market(player_name: str, owner_team_name: str | None = None) -> dict:
    universe = find_one("player_universe", player_name, owner_team_name)
    identity = find_one("player_identity_context", player_name)
    asset = find_one("roster_asset_values", player_name, owner_team_name)
    rec = find_one("player_recommendations", player_name, owner_team_name)

    return {
        "market_pool": universe.get("market_pool"),
        "estimated_market_value": num(universe.get("estimated_market_value")),
        "market_consensus_score": num(universe.get("market_consensus_score")),
        "market_score": num(identity.get("market_score")),
        "market_liquidity_score": num(pick(asset.get("market_liquidity_score"), rec.get("market_liquidity_score"))),
        "priority_score": num(rec.get("priority_score")),
        "_raw": {
            "player_universe": universe,
            "player_identity_context": identity,
            "roster_asset_values": asset,
            "player_recommendations": rec,
        },
    }
