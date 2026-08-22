def _num(v, default=50):
    try:
        return float(v)
    except Exception:
        return default


class RiskAgent:
    name = "risk"

    def evaluate(self, row: dict) -> dict:
        source = row.get("source")
        team = row.get("nfl_team") or row.get("team")
        pos = row.get("pos") or "-"

        risk = _num(row.get("risk_score"), 45)

        flags = []

        if source in [None, "", "computed_from_player_universe"]:
            risk += 15
            flags.append("weak source")

        if not team or str(team).strip() in ["", "-", "None", "FA"]:
            risk += 20
            flags.append("no team context")

        if pos == "RB":
            risk += 5
            flags.append("RB volatility")

        risk = max(0, min(100, risk))

        confidence = max(25, 100 - risk * 0.5)

        return {
            "agent": self.name,
            "grade": round(100 - risk, 2),
            "risk": round(risk, 2),
            "flags": flags,
            "confidence": round(confidence, 2),
        }
