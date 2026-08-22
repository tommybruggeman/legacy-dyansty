from copy import deepcopy
from snapshot.intelligence.rookies.rookie_marginal_ev_engine import RookieMarginalEVEngine


class RookieDraftSimulator:
    """
    True draft simulator:
    - simulates board state
    - removes selections
    - recalculates EV landscape
    """

    def __init__(self):
        self.ev_engine = RookieMarginalEVEngine()

    # -----------------------------
    # BUILD INITIAL BOARD
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
                "confidence": v["confidence"]
            })

        return board

    # -----------------------------
    # SIMULATE PICK
    # -----------------------------
    def simulate_pick(self, board, selected_archetype):
        new_board = deepcopy(board)

        # remove selected archetype
        new_board = [
            b for b in new_board
            if b["archetype"] != selected_archetype
        ]

        # recompute relative value drop
        for b in new_board:
            if b["pos"] == selected_archetype.split("_")[0]:
                b["ev"] *= 0.95  # mild positional saturation penalty

        return new_board

    # -----------------------------
    # DRAFT SLOT DECISION
    # -----------------------------
    def pick(self, slot: float):
        board = self.build_board()

        results = []

        for option in board:

            simulated_board = self.simulate_pick(board, option["archetype"])

            best_remaining = max(simulated_board, key=lambda x: x["ev"])

            marginal_gain = option["ev"] - best_remaining["ev"] * 0.5

            results.append({
                "archetype": option["archetype"],
                "pos": option["pos"],
                "ev": option["ev"],
                "marginal_gain": round(marginal_gain, 3)
            })

        ranked = sorted(results, key=lambda x: x["marginal_gain"], reverse=True)

        best = ranked[0]

        # trade logic
        trade_down = best["marginal_gain"] < 1.5

        return {
            "pick": slot,
            "best_pick": best,
            "top_options": ranked[:5],
            "trade_down_recommended": trade_down
        }
