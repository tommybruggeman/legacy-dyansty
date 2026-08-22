def _num(v, default=50):
    try:
        return float(v)
    except Exception:
        return default


def _has_team(row):
    team = row.get("nfl_team") or row.get("team") or row.get("current_team")
    return bool(team and str(team).strip() not in ["", "-", "None", "FA"])


class SituationAgent:
    name = "situation"

    def evaluate(self, row: dict) -> dict:
        pos = row.get("pos") or "-"
        fit = _num(row.get("team_need_fit_score") or row.get("situation_score"), 45)

        if not _has_team(row):
            return {
                "agent": self.name,
                "grade": 25,
                "opportunity": 20,
                "timeline": "unknown",
                "risk": 80,
                "summary": "No confirmed team context.",
                "confidence": 35,
            }

        opportunity = fit

        if pos == "QB":
            timeline = "developmental unless immediate starting path appears"
            risk = 65
        elif pos == "RB":
            timeline = "can gain value quickly if role opens"
            risk = 55
        elif pos == "WR":
            timeline = "usually needs target-earning proof"
            risk = 50
        elif pos == "TE":
            timeline = "longer development timeline"
            risk = 60
        else:
            timeline = "unclear"
            risk = 60

        return {
            "agent": self.name,
            "grade": round(opportunity, 2),
            "opportunity": round(opportunity, 2),
            "timeline": timeline,
            "risk": risk,
            "summary": f"Team context exists; {timeline}.",
            "confidence": 65,
        }
