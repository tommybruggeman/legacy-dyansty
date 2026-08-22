from typing import Dict, Any


def _num(value, default=50.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class SituationAgent:
    """
    Evaluates role/opportunity/context only.
    No talent or final recommendation.
    """

    def evaluate(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        situation = graph.get("situation", {}) or {}

        situation_score = _num(situation.get("situation_score"), 50)
        role_score = _num(situation.get("role_score"), situation_score)
        depth_chart_score = _num(situation.get("depth_chart_score"), situation_score)
        confirmed = situation.get("team_context_confirmed")

        score = round(
            situation_score * 0.50
            + role_score * 0.30
            + depth_chart_score * 0.20,
            2,
        )

        if confirmed is False:
            confidence = "LOW"
        elif confirmed is True:
            confidence = "HIGH"
        else:
            confidence = "MEDIUM"

        if score >= 75:
            tier = "Strong opportunity"
        elif score >= 60:
            tier = "Usable opportunity"
        elif score >= 45:
            tier = "Unclear opportunity"
        else:
            tier = "Blocked opportunity"

        return {
            "agent": "SituationAgent",
            "score": score,
            "tier": tier,
            "confidence": confidence,
            "summary": f"{graph.get('player_name')} has {tier.lower()} with {confidence.lower()} context confidence.",
            "inputs": {
                "situation_score": situation_score,
                "role_score": role_score,
                "depth_chart_score": depth_chart_score,
                "team_context_confirmed": confirmed,
            },
        }


def evaluate_situation(graph: Dict[str, Any]) -> Dict[str, Any]:
    return SituationAgent().evaluate(graph)
