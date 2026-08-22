class DecisionAgent:
    name = "decision"

    def synthesize(self, row: dict, dossier: dict, mode: str = "dynasty") -> dict:
        scout = dossier.get("scout", {})
        situation = dossier.get("situation", {})
        market = dossier.get("market", {})
        contract = dossier.get("contract", {})
        risk = dossier.get("risk", {})

        if mode == "rookie":
            score = (
                scout.get("grade", 50) * 0.42
                + situation.get("grade", 50) * 0.18
                + market.get("grade", 50) * 0.20
                + risk.get("grade", 50) * 0.20
            )
        else:
            score = (
                scout.get("grade", 50) * 0.25
                + situation.get("grade", 50) * 0.20
                + market.get("grade", 50) * 0.20
                + contract.get("grade", 50) * 0.15
                + risk.get("grade", 50) * 0.20
            )

        if score >= 75:
            recommendation = "TARGET / BUILD AROUND"
        elif score >= 65:
            recommendation = "HOLD / DRAFTABLE"
        elif score >= 55:
            recommendation = "WATCHLIST / PRICE SENSITIVE"
        else:
            recommendation = "AVOID UNLESS CHEAP"

        confidence = round(
            (
                scout.get("confidence", 50)
                + situation.get("confidence", 50)
                + market.get("confidence", 50)
                + risk.get("confidence", 50)
            )
            / 4,
            2,
        )

        name = row.get("player_name") or row.get("name") or "Unknown"
        pos = row.get("pos") or "-"
        team = row.get("nfl_team") or row.get("team") or "-"

        return {
            "agent": self.name,
            "decision_score": round(score, 2),
            "recommendation": recommendation,
            "confidence": confidence,
            "summary": (
                f"{name} ({pos}, {team}) is {recommendation}. "
                f"Scout {scout.get('grade')}, situation {situation.get('grade')}, "
                f"market {market.get('grade')}, risk {risk.get('risk')}."
            ),
        }
