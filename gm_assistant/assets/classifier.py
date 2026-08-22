from __future__ import annotations


class AssetClassifier:
    """
    Canonical asset classification.

    Every GM engine should trust this instead of making its own
    rookie / FA / roster logic.
    """

    def classify(self, player: dict) -> dict:

        rookie_year = player.get("rookie_class_year")
        owner = player.get("current_owner")
        active = player.get("active", True)

        asset = {
            "asset_category": "PLAYER",
            "asset_subtype": "VETERAN",
            "tradeable": True,
            "rostered": owner is not None,
            "active_player": bool(active),
            "market_pool": None,
        }

        if not active:
            asset["asset_subtype"] = "RETIRED"
            asset["tradeable"] = False
            asset["market_pool"] = "NONE"
            return asset

        if rookie_year:
            asset["asset_subtype"] = "ROOKIE"

        if owner:
            asset["market_pool"] = "TRADE"
        else:
            asset["market_pool"] = "FREE_AGENT"

        return asset
