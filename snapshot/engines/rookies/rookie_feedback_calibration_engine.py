from __future__ import annotations


class RookieFeedbackCalibrationEngine:
    """
    Uses historical prediction error to adjust model sensitivities.

    This is NOT prediction.
    This is learning how wrong we were.
    """

    def __init__(self):
        # default weights (will evolve over time)
        self.weights = {
            "qb_start_prob": 1.0,
            "workload_prob": 1.0,
            "target_share_prob": 1.0,
            "separation": 1.0,
            "red_zone_role": 1.0,
            "draft_signal": 1.0,
            "landing_environment": 1.0,
        }

    def update_weights_from_feedback(self, feedback_rows: list[dict]) -> dict:
        """
        Adjust weights based on historical prediction error.
        """

        if not feedback_rows:
            return self.weights

        # accumulate directional errors
        error_bias = {
            "QB": 0.0,
            "RB": 0.0,
            "WR": 0.0,
            "TE": 0.0,
        }

        counts = {
            "QB": 0,
            "RB": 0,
            "WR": 0,
            "TE": 0,
        }

        for r in feedback_rows:
            pos = r.get("pos", "WR")
            error = float(r.get("prediction_error", 0))

            error_bias[pos] = error_bias.get(pos, 0) + error
            counts[pos] = counts.get(pos, 0) + 1

        # normalize + convert to adjustment signals
        adjustments = {}

        for pos in counts:
            if counts[pos] == 0:
                continue

            avg_error = error_bias[pos] / counts[pos]

            # interpret error:
            # positive error = model overrating that position archetype
            if avg_error > 5:
                adjustments[pos] = 0.95
            elif avg_error < -5:
                adjustments[pos] = 1.05
            else:
                adjustments[pos] = 1.0

        # apply position-level adjustments into feature weights
        # (simple but powerful first version)
        if "QB" in adjustments:
            self.weights["qb_start_prob"] *= adjustments["QB"]

        if "RB" in adjustments:
            self.weights["workload_prob"] *= adjustments["RB"]

        if "WR" in adjustments:
            self.weights["target_share_prob"] *= adjustments["WR"]

        if "TE" in adjustments:
            self.weights["red_zone_role"] *= adjustments["TE"]

        return self.weights
