from copy import deepcopy
from snapshot.intelligence.rookies.rookie_marginal_ev_engine import RookieMarginalEVEngine


class RookieDraftSimulatorV2:
    """
    TRUE draft ecosystem simulator.

    Fixes:
    - QB over-dominance
    - static EV bias
    - no supply depletion
    """

    def __init__(self):
        self.ev_engine = RookieMarginalEVEngine()

    # -----------------------------
    # INITIAL BOARD
    # -----------------------------
    def build_board(self):
        data = self.ev_engine.intel.build()
        strategy = data["strategy"]

        board = []

        for archetype, v in strategy.items():
            pos = archetype.split("_")[0]

            board.append({
                "archetype": archetype,
                "pos": pos,
                "ev": v["roi_score"],
                "base_ev": v["roi_score"],
                "confidence": v["confidence"]
            })

        return board

    # -----------------------------
    # POSITIONAL PRESSURE MODEL
    # -----------------------------
    def apply_pressure(self, board):
        """
        This is the key upgrade:
        remaining supply changes value curves
        """

        pos_counts = {}

        for b in board:
            pos_counts[b["pos"]] = pos_counts.get(b["pos"], 0) + 1

        for b in board:
            pos = b["pos"]

            # RB scarcity collapses faster as pool shrinks
            if pos == "RB":
                pressure = 1.0 + (0.3 / max(pos_counts[pos], 1))
                b["ev"] *= pressure

            # WR stable depth curve
            if pos == "WR":
                pressure = 1.0 + (0.1 / max(pos_counts[pos], 1))
                b["ev"] *= pressure

            # QB top-heavy collapse
            if pos == "QB":
                pressure = 1.0 - (0.2 / max(pos_counts[pos], 1))
                b["ev"] *= pressure

            # TE late surge
            if pos == "TE":
                pressure = 1.0 + (0.15 / max(pos_counts[pos], 1))
                b["ev"] *= pressure

        return board

    # -----------------------------
    # SIMULATE PICK
    # -----------------------------
    def simulate_pick(self, board, selected_archetype):

        new_board = [b for b in board if b["archetype"] != selected_archetype]

        # apply ecosystem pressure AFTER pick
        new_board = self.apply_pressure(new_board)

        return new_board

    # -----------------------------
    # MARGINAL VALUE
    # -----------------------------
    def pick(self, slot: float, roster_needs=None):
        roster_needs = roster_needs or []

        board = self.build_board()
        results = []

        for option in board:

            simulated = self.simulate_pick(board, option["archetype"])

            best_remaining = max(simulated, key=lambda x: x["ev"])

            # TRUE marginal gain (clean difference, no artificial scaling)
            marginal_gain = option["ev"] - best_remaining["ev"]

            # roster adjustment
            for need in roster_needs:
                if need.upper() == option["pos"]:
                    marginal_gain *= 1.15

            results.append({
                "archetype": option["archetype"],
                "pos": option["pos"],
                "ev": round(option["ev"], 3),
                "marginal_gain": round(marginal_gain, 3)
            })

        ranked = sorted(results, key=lambda x: x["marginal_gain"], reverse=True)

        best = ranked[0]

        # REAL trade-down logic (based on separation, not absolute value)
        ev_gap = ranked[0]["marginal_gain"] - ranked[1]["marginal_gain"]

        trade_down = ev_gap < 0.75

        return {
            "pick": slot,
            "best_pick": best,
            "top_options": ranked[:5],
            "ev_gap": round(ev_gap, 3),
            "trade_down_recommended": trade_down
        }
