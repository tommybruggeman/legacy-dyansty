
import pandas as pd

class PredictionFeedbackEngine:

    def build_record(self, prediction, actual):
        return {
            "sleeper_id": prediction["sleeper_id"],
            "predicted_mean": prediction["mean"],
            "predicted_floor": prediction["floor"],
            "predicted_ceiling": prediction["ceiling"],
            "actual_points": actual,
            "error": actual - prediction["mean"],
            "abs_error": abs(actual - prediction["mean"]),
        }

    def aggregate_errors(self, df):
        return {
            "mean_error": float(df["error"].mean()),
            "mean_abs_error": float(df["abs_error"].mean()),
            "bias": "over" if df["error"].mean() < 0 else "under"
        }
