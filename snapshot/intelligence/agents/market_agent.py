def _num(v, default=50):
    try:
        return float(v)
    except Exception:
        return default


class MarketAgent:
    name = "market"

    def evaluate(self, row: dict) -> dict:
        pos = row.get("pos") or "-"
        score = _num(
            row.get("market_score")
            or row.get("trade_value_score")
            or row.get("final_rookie_score")
            or row.get("rank_score"),
            50,
        )

        if pos == "QB":
            scarcity = 85
        elif pos == "RB":
            scarcity = 55
        elif pos == "WR":
            scarcity = 70
        elif pos == "TE":
            scarcity = 50
        else:
            scarcity = 45

        market_grade = score * 0.65 + scarcity * 0.35

        if market_grade >= 70:
            stance = "market supports value"
        elif market_grade >= 55:
            stance = "market is neutral"
        else:
            stance = "market value is fragile"

        return {
            "agent": self.name,
            "grade": round(market_grade, 2),
            "scarcity": scarcity,
            "stance": stance,
            "confidence": 60,
        }
