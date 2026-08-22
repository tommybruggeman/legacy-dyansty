from typing import Dict, Any


def _num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class ProjectionAgent:
    """
    Reads 1-year, 2-year, 3-year projection context.
    Higher score = better expected rookie-deal value.
    """

    def evaluate(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        projection = graph.get("projection", {}) or {}

        y1 = _num(projection.get("year_1_projected_points"), 0)
        y2 = _num(projection.get("year_2_projected_points"), 0)
        y3 = _num(projection.get("year_3_projected_points"), 0)

        y1_start = _num(projection.get("year_1_start_probability"), 0)
        y2_start = _num(projection.get("year_2_start_probability"), 0)
        y3_start = _num(projection.get("year_3_start_probability"), 0)

        three_year_points = y1 + y2 + y3
        weighted_start_path = (y1_start * 0.45) + (y2_start * 0.35) + (y3_start * 0.20)

        # 3-year fantasy value scale:
        # 0 pts = 0
        # 300 pts = 60
        # 500 pts = 80
        # 700+ pts = 100
        points_score = min(100, three_year_points / 7)
        start_score = weighted_start_path

        score = round((points_score * 0.65) + (start_score * 0.35), 2)

        if score >= 80:
            tier = "Strong 3-year projection"
        elif score >= 65:
            tier = "Usable 3-year projection"
        elif score >= 50:
            tier = "Developmental projection"
        else:
            tier = "Low projection confidence"

        return {
            "agent": "ProjectionAgent",
            "score": score,
            "tier": tier,
            "summary": f"{graph.get('player_name')} has {tier.lower()}.",
            "inputs": {
                "year_1_projected_points": y1,
                "year_2_projected_points": y2,
                "year_3_projected_points": y3,
                "three_year_projected_points": three_year_points,
                "year_1_start_probability": y1_start,
                "year_2_start_probability": y2_start,
                "year_3_start_probability": y3_start,
                "weighted_start_path": round(weighted_start_path, 2),
            },
        }


def evaluate_projection(graph: Dict[str, Any]) -> Dict[str, Any]:
    return ProjectionAgent().evaluate(graph)
