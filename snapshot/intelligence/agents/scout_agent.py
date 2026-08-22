def _num(v, default=50):
    try:
        return float(v)
    except Exception:
        return default


class ScoutAgent:
    name = "scout"

    def evaluate(self, row: dict) -> dict:
        pos = row.get("pos") or "-"
        prospect = _num(row.get("prospect_score"), 50)
        future = _num(row.get("future_score"), prospect)

        ceiling = min(100, prospect * 0.65 + future * 0.35 + 8)
        floor = max(0, prospect * 0.55 + future * 0.25 - 8)
        bust_risk = max(5, min(95, 100 - floor))

        if pos == "QB":
            trait = "superflex premium profile"
        elif pos == "RB":
            trait = "role and landing-spot sensitive"
        elif pos == "WR":
            trait = "dynasty value insulation profile"
        elif pos == "TE":
            trait = "slower development curve"
        else:
            trait = "unknown positional profile"

        grade = round(prospect * 0.65 + future * 0.35, 2)

        return {
            "agent": self.name,
            "grade": grade,
            "ceiling": round(ceiling, 2),
            "floor": round(floor, 2),
            "bust_risk": round(bust_risk, 2),
            "trait_summary": trait,
            "confidence": 60 if row.get("source") == "computed_from_player_universe" else 80,
        }
