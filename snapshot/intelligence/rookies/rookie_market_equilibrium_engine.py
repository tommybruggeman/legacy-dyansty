from snapshot.intelligence.rookies.rookie_marginal_ev_engine import RookieMarginalEVEngine


class RookieMarketEquilibriumEngine:
    """
    Fixes positional distortion by simulating market response
    after each selection.
    """

    def __init__(self):
        self.ev_engine = RookieMarginalEVEngine()

    # -----------------------------
    # MARKET STATE UPDATE
    # -----------------------------
    def apply_market_shift(self, board):
        """
        This is the key correction layer:
        positions react to scarcity changes.
        """

        pos_counts = {}
        for b in board:
            pos_counts[b["pos"]] = pos_counts.get(b["pos"], 0) + 1

        for b in board:
            pos = b["pos"]

            # ---------------- RB: saturation reduces marginal utility
            if pos == "RB":
                saturation = 1.0 - (0.25 / max(pos_counts[pos], 1))
                b["ev"] *= saturation

            # ---------------- WR: relative inflation (depth advantage)
            if pos == "WR":
                inflation = 1.0 + (0.15 / max(pos_counts[pos], 1))
                b["ev"] *= inflation

            # ---------------- QB: becomes binary (elite-only value)
            if pos == "QB":
                concentration = 1.0 + (0.2 / max(pos_counts[pos], 1))
                b["ev"] *= concentration

            # ---------------- TE: late curve inflation
            if pos == "TE":
                late_value = 1.0 + (0.1 / max(pos_counts[pos], 1))
                b["ev"] *= late_value

        return board

    # -----------------------------
    # SIMULATE PICK EFFECT
    # -----------------------------
    def simulate_pick(self, board, selected_archetype):

        new_board = [b for b in board if b["archetype"] != selected_archetype]

        # market reacts AFTER selection
        new_board = self.apply_market_shift(new_board)

        return new_board

    # -----------------------------
    # DRAFT DECISION
    # -----------------------------
    def pick(self, slot: float, roster_needs=None):
        roster_needs = roster_needs or []

        data = self.ev_engine.intel.build()
        strategy = data["strategy"]

        board = []

        for archetype, v in strategy.items():
            board.append({
                "archetype": archetype,
                "pos": archetype.split("_")[0],
                "ev": v["roi_score"]
            })

        results = []

        for option in board:

            simulated = self.simulate_pick(board, option["archetype"])

            best_remaining = max(simulated, key=lambda x: x["ev"])

            # TRUE marginal gain (clean equilibrium difference)
            marginal_gain = option["ev"] - best_remaining["ev"]

            # roster adjustment
            for need in roster_needs:
                if need.upper() == option["pos"]:
                    marginal_gain *= 1.15

            results.append({
                "archetype": option["archetype"],
                "pos": option["pos"],
                "ev": option["ev"],
                "marginal_gain": round(marginal_gain, 3)
            })

        ranked = sorted(results, key=lambda x: x["marginal_gain"], reverse=True)

        best = ranked[0]

        ev_gap = ranked[0]["marginal_gain"] - ranked[1]["marginal_gain"]

        trade_down = ev_gap < 0.75

        return {
            "pick": slot,
            "best_pick": best,
            "top_options": ranked[:5],
            "ev_gap": round(ev_gap, 3),
            "trade_down_recommended": trade_down
        }
