from snapshot.intelligence.rookies.rookie_intelligence_engine import RookieIntelligenceEngine


class RookieDraftDecisionEngine:
    """
    Converts ROI intelligence → actual draft decisions.
    """

    def __init__(self):
        self.intel = RookieIntelligenceEngine()

    # -----------------------------
    # CORE DECISION LOGIC
    # -----------------------------
    def recommend(self, pick: float, roster_needs: list[str] = None):
        data = self.intel.build()
        strategy = data["strategy"]

        roster_needs = roster_needs or []

        # flatten into sortable list
        options = []

        for archetype, v in strategy.items():
            options.append({
                "archetype": archetype,
                "roi": v["roi_score"],
                "year1": v["year1"],
                "year3": v["year3"],
                "confidence": v["confidence"],
            })

        # -----------------------------
        # ADJUSTMENT RULES
        # -----------------------------
        def adjust(o):
            score = o["roi"]

            # early pick bonus for high-ceiling positions
            if pick <= 1.08:
                if "QB" in o["archetype"]:
                    score += 1.5  # QB premium early
                if "WR" in o["archetype"]:
                    score += 1.0

            # RB volatility penalty in early 1st
            if pick <= 1.06 and "RB" in o["archetype"]:
                score -= 1.2

            # roster need boost
            for need in roster_needs:
                if need.upper() in o["archetype"]:
                    score += 0.8

            # confidence weighting
            score *= (0.5 + o["confidence"])

            return score

        ranked = sorted(options, key=adjust, reverse=True)

        best = ranked[0]

        # trade-down logic
        trade_down_flag = False
        if best["roi"] < 3.0 and pick <= 1.05:
            trade_down_flag = True

        return {
            "pick": pick,
            "recommendation": best,
            "all_options": ranked[:5],
            "trade_down_recommended": trade_down_flag
        }
