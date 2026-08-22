from typing import Dict, Any


def _num(value, default=50.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class DecisionAgent:
    """
    Reads agent outputs and makes final actionable fantasy recommendation.
    This is the first layer allowed to say buy/sell/hold/draft/etc.
    """

    def decide(self, graph: Dict[str, Any], scout: Dict[str, Any], situation: Dict[str, Any], risk: Dict[str, Any], market: Dict[str, Any] = None, projection: Dict[str, Any] = None) -> Dict[str, Any]:
        player_name = graph.get("player_name")
        pos = graph.get("pos")
        team = graph.get("nfl_team") or "-"

        scout_score = _num(scout.get("score"), 50)
        situation_score = _num(situation.get("score"), 50)
        risk_score = _num(risk.get("score"), 60)

        market_score = _num((market or {}).get("score"), scout_score)
        projection_score = _num((projection or {}).get("score"), 50)

        final_score = round(
            projection_score * 0.40
            + situation_score * 0.20
            + risk_score * 0.20
            + scout_score * 0.15
            + market_score * 0.05,
            2,
        )

        if projection_score < 50 and scout_score >= 70 and market_score >= 70:
            recommendation = "INSUFFICIENT DATA / PROVISIONAL"
        elif final_score >= 78 and scout_score >= 75:
            recommendation = "TARGET / DRAFT"
        elif final_score >= 68:
            recommendation = "HOLD / DRAFTABLE"
        elif final_score >= 58:
            recommendation = "WATCHLIST / STASH"
        elif risk_score < 45:
            recommendation = "AVOID"
        else:
            recommendation = "CHURN / REPLACE"

        confidence = round((scout_score + situation_score + risk_score) / 3, 2)

        return {
            "agent": "DecisionAgent",
            "score": final_score,
            "recommendation": recommendation,
            "confidence": confidence,
            "summary": (
                f"{player_name} ({pos}, {team}) is {recommendation}. "
                f"Projection {projection_score}, situation {situation_score}, risk {risk_score}, scout {scout_score}, market {market_score}."
            ),
            "inputs": {
                "scout_score": scout_score,
                "situation_score": situation_score,
                "market_score": market_score,
                "projection_score": projection_score,
                "risk_score": risk_score,
            },
        }


def make_decision(graph: Dict[str, Any], scout: Dict[str, Any], situation: Dict[str, Any], risk: Dict[str, Any], market: Dict[str, Any] = None, projection: Dict[str, Any] = None) -> Dict[str, Any]:
    return DecisionAgent().decide(graph, scout, situation, risk, market, projection)
