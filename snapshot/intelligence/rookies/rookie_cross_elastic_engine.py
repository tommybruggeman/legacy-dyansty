from snapshot.engines.rookies.rookie_player_mapper import RookiePlayerMapper
from snapshot.intelligence.rookies.rookie_marginal_ev_engine import RookieMarginalEVEngine


class RookieCrossElasticEngine:
    """
    FIXES CORE MODEL ERROR:

    Positions are NOT independent markets.
    They are competing allocations of draft capital.
    """

    def __init__(self):
        self.mapper = RookiePlayerMapper()
        self.ev_engine = RookieMarginalEVEngine()

    # -----------------------------
    # CROSS-POSITION MARKET SHIFT
    # -----------------------------
    def apply_cross_elasticity(self, board):
        """
        If one position becomes dominant,
        others become relatively more valuable.
        """

        pos_strength = {}

        for b in board:
            pos = b["pos"]
            pos_strength[pos] = pos_strength.get(pos, 0) + b["ev"]

        total = sum(pos_strength.values()) + 1e-9

        # market share of each position
        market_share = {
            k: v / total for k, v in pos_strength.items()
        }

        for b in board:
            pos = b["pos"]

            share = market_share.get(pos, 0.25)

            # -----------------------------
            # CROSS-ELASTIC RESPONSE CURVE
            # -----------------------------

            # QB dominance forces RB/WR inflation
            if pos == "QB":
                b["ev"] *= (1.0 - 0.2 * share)

            if pos == "RB":
                # RB becomes more valuable when QB/WR dominate market share
                b["ev"] *= (1.0 + 0.35 * (1 - share))

            if pos == "WR":
                # WR is stabilizer position
                b["ev"] *= (1.0 + 0.15 * (1 - abs(share - 0.33)))

            if pos == "TE":
                # TE becomes arbitrage when others dominate
                b["ev"] *= (1.0 + 0.25 * (1 - share))

        return board

    # -----------------------------
    # SIMULATION STEP
    # -----------------------------
    def simulate_pick(self, board, selected_archetype):
        new_board = [b for b in board if b["archetype"] != selected_archetype]

        # apply cross-market reaction AFTER selection
        new_board = self.apply_cross_elasticity(new_board)

        return new_board

    # -----------------------------
    # DRAFT PICK ENGINE
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

            marginal_gain = option["ev"] - best_remaining["ev"]

            # roster need adjustment
            for need in roster_needs:
                if need.upper() == option["pos"]:
                    marginal_gain *= 1.12

            results.append({
                "archetype": option["archetype"],
                "pos": option["pos"],
                "ev": round(option["ev"], 3),
                "marginal_gain": round(marginal_gain, 3)
            })

        ranked = sorted(results, key=lambda x: x["marginal_gain"], reverse=True)

        best = ranked[0]

        ev_gap = ranked[0]["marginal_gain"] - ranked[1]["marginal_gain"]

        trade_down = ev_gap < 0.65

        best_player = self.mapper.best_player(best["pos"])
        best["player"] = best_player
        return {
            "pick": slot,
            


"best_pick": {
    **best,
    "player": best_player
},

            "top_options": ranked[:5],
            "ev_gap": round(ev_gap, 3),
            "trade_down_recommended": trade_down
        }
