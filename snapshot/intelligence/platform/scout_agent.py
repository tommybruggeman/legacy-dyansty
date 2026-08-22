from typing import Dict, Any


def _num(value, default=50.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class ScoutAgent:
    """
    Evaluates player talent/profile only.
    No situation, contract, or final recommendation.
    """

    def evaluate(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        pos = graph.get("pos") or "UNK"
        rookie = graph.get("rookie", {}) or {}
        market = graph.get("market", {}) or {}
        production = graph.get("production", {}) or {}

        rookie_score = _num(rookie.get("rookie_score"), 50)
        asset_score = _num(market.get("asset_score"), rookie_score)
        ppg = _num(production.get("season_ppg"), 0)

        score = round(rookie_score, 2)

        if score >= 80:
            tier = "Premium talent"
        elif score >= 70:
            tier = "Draftable / stash talent"
        elif score >= 60:
            tier = "Watchlist talent"
        else:
            tier = "Replacement-level profile"

        return {
            "agent": "ScoutAgent",
            "score": score,
            "tier": tier,
            "pos": pos,
            "summary": f"{graph.get('player_name')} profiles as {tier} based on raw talent indicators.",
            "inputs": {
                "rookie_score": rookie_score,
                "asset_score": asset_score,
                "season_ppg": ppg,
            },
        }


def evaluate_scout(graph: Dict[str, Any]) -> Dict[str, Any]:
    return ScoutAgent().evaluate(graph)
