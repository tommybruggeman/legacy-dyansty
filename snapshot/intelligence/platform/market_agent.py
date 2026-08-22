from typing import Dict, Any


def _num(value, default=50.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class MarketAgent:
    """
    Evaluates dynasty/rookie market value only.
    No final recommendation.
    """

    def evaluate(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        market = graph.get("market", {}) or {}
        rookie = graph.get("rookie", {}) or {}
        identity = graph.get("identity", {}) or {}

        market_score = _num(market.get("market_score"), None)
        asset_score = _num(market.get("asset_score"), None)
        trade_value = _num(market.get("trade_value"), None)
        adp = _num(market.get("adp"), None)
        rookie_score = _num(rookie.get("rookie_score"), 50)

        pos = graph.get("pos") or identity.get("pos") or "UNK"

        pos_bonus = 0

        inputs = []

        if market_score is not None:
            inputs.append(market_score)
        if asset_score is not None:
            inputs.append(asset_score)
        if trade_value is not None:
            inputs.append(trade_value)
        if adp is not None:
            # lower ADP is better
            adp_score = max(20, min(95, 100 - adp))
            inputs.append(adp_score)

        if not inputs:
            inputs.append(rookie_score)

        score = round((sum(inputs) / len(inputs)), 2)
        score = round(max(20, min(95, score)), 2)

        if score >= 80:
            tier = "Strong market asset"
        elif score >= 68:
            tier = "Draftable market asset"
        elif score >= 55:
            tier = "Thin market asset"
        else:
            tier = "Weak market asset"

        return {
            "agent": "MarketAgent",
            "score": score,
            "tier": tier,
            "summary": f"{graph.get('player_name')} carries {tier.lower()} value.",
            "inputs": {
                "market_score": market_score,
                "asset_score": asset_score,
                "trade_value": trade_value,
                "adp": adp,
                "rookie_score_fallback": rookie_score,
                "pos_bonus": pos_bonus,
            },
        }


def evaluate_market(graph: Dict[str, Any]) -> Dict[str, Any]:
    return MarketAgent().evaluate(graph)
