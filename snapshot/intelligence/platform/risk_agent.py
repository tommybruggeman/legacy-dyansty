from typing import Dict, Any


def _num(value, default=50.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class RiskAgent:
    """
    Evaluates ways a player can fail.
    Higher score = safer.
    """

    def evaluate(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        risk = graph.get("risk", {}) or {}
        contract = graph.get("contract", {}) or {}

        risk_score = _num(risk.get("risk_score"), 60)
        injury_risk = _num(risk.get("injury_risk"), 40)
        age_risk = _num(risk.get("age_risk"), 35)
        contract_risk = _num(risk.get("contract_risk"), 35)

        salary = _num(contract.get("salary"), 0)
        years = _num(contract.get("years"), 0)

        contract_penalty = min(salary * 0.4 + years * 2, 20)

        safety_score = round(
            risk_score * 0.55
            + (100 - injury_risk) * 0.15
            + (100 - age_risk) * 0.15
            + (100 - contract_risk) * 0.10
            - contract_penalty * 0.05,
            2,
        )

        if safety_score >= 75:
            tier = "Low risk"
        elif safety_score >= 60:
            tier = "Normal risk"
        elif safety_score >= 45:
            tier = "Elevated risk"
        else:
            tier = "High risk"

        return {
            "agent": "RiskAgent",
            "score": safety_score,
            "tier": tier,
            "summary": f"{graph.get('player_name')} carries {tier.lower()} based on raw risk indicators.",
            "inputs": {
                "risk_score": risk_score,
                "injury_risk": injury_risk,
                "age_risk": age_risk,
                "contract_risk": contract_risk,
                "salary": salary,
                "years": years,
            },
        }


def evaluate_risk(graph: Dict[str, Any]) -> Dict[str, Any]:
    return RiskAgent().evaluate(graph)
