from snapshot.intelligence.rookies.rookie_intelligence_engine import RookieIntelligenceEngine


class RookieMarginalEVEngine:
    """
    Converts ROI into draft decisions using marginal opportunity cost.

    This is the first TRUE draft decision model layer.
    """

    def __init__(self):
        self.intel = RookieIntelligenceEngine()

    # -----------------------------
    # POSITION SCARCITY CURVES
    # -----------------------------
    def scarcity(self, pos: str, pick: float) -> float:
        pos = pos.upper()

        # RB is scarce early
        if pos == "RB":
            return 1.2 if pick <= 1.06 else 1.0

        # WR is stable depth
        if pos == "WR":
            return 1.0

        # QB spikes early in SF formats (we approximate here)
        if pos == "QB":
            return 1.1 if pick <= 1.05 else 0.9

        # TE is delayed value curve
        if pos == "TE":
            return 0.8 if pick <= 1.06 else 1.1

        return 1.0

    # -----------------------------
    # REPLACEMENT VALUE CURVE
    # -----------------------------
    def replacement_penalty(self, pos: str, pick: float) -> float:
        pos = pos.upper()

        # early RB replacement cost is high
        if pos == "RB" and pick <= 1.06:
            return 1.3

        # WR has flatter replacement curve
        if pos == "WR":
            return 1.0

        # QB replacement cost is moderate
        if pos == "QB":
            return 1.1

        # TE late surge reduces penalty
        if pos == "TE" and pick > 1.06:
            return 1.2

        return 1.0

    # -----------------------------
    # MARGINAL EV CALCULATION
    # -----------------------------
    def marginal_value(self, pick: float, roster_needs=None):
        roster_needs = roster_needs or []

        data = self.intel.build()
        strategy = data["strategy"]

        candidates = []

        for archetype, v in strategy.items():

            pos = archetype.split("_")[0]

            base = v["roi_score"]

            scarcity = self.scarcity(pos, pick)
            replacement = self.replacement_penalty(pos, pick)

            need_boost = 1.0
            for n in roster_needs:
                if n.upper() in pos:
                    need_boost += 0.15

            ev = base * scarcity * need_boost * replacement

            candidates.append({
                "archetype": archetype,
                "pos": pos,
                "base_roi": base,
                "ev": round(ev, 3),
                "scarcity": scarcity,
                "replacement": replacement
            })

        ranked = sorted(candidates, key=lambda x: x["ev"], reverse=True)

        best = ranked[0]

        # trade-down logic (true marginal version)
        ev_gap = ranked[0]["ev"] - ranked[1]["ev"]

        trade_down = ev_gap < 1.0  # small separation = trade down opportunity

        return {
            "pick": pick,
            "best_pick": best,
            "top_options": ranked[:5],
            "ev_gap": round(ev_gap, 3),
            "trade_down_recommended": trade_down
        }
