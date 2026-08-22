
import pandas as pd
from datetime import datetime

class PredictionRegistryEngine:

    def __init__(self):
        self.buffer = []

    # -----------------------------------
    # STORE PREDICTION (CORE MEMORY)
    # -----------------------------------
    def log_prediction(self, prediction, context, meta=None):

        record = {
            "timestamp": datetime.utcnow().isoformat(),

            "sleeper_id": prediction.get("sleeper_id"),
            "player_name": prediction.get("player_name"),

            "predicted_mean": prediction.get("mean"),
            "predicted_floor": prediction.get("floor"),
            "predicted_ceiling": prediction.get("ceiling"),

            "context_score": context.get("context_score") if context else None,
            "volatility": context.get("volatility") if context else None,

            "meta": meta or {}
        }

        self.buffer.append(record)
        return record

    # -----------------------------------
    # ATTACH ACTUAL OUTCOME
    # -----------------------------------
    def attach_actual(self, prediction_record, actual_points):

        prediction_record["actual_points"] = actual_points
        prediction_record["error"] = actual_points - prediction_record["predicted_mean"]
        prediction_record["abs_error"] = abs(prediction_record["error"])

        return prediction_record

    # -----------------------------------
    # BUILD DATAFRAME FOR LEARNING
    # -----------------------------------
    def to_dataframe(self):
        return pd.DataFrame(self.buffer)

    # -----------------------------------
    # LEARNING SIGNALS
    # -----------------------------------
    def learning_summary(self):

        if not self.buffer:
            return {"status": "empty"}

        df = pd.DataFrame(self.buffer)

        if "error" not in df.columns:
            return {"status": "no_actuals_yet"}

        return {
            "mean_error": float(df["error"].mean()),
            "mean_abs_error": float(df["abs_error"].mean()),
            "bias": "overpredicting" if df["error"].mean() < 0 else "underpredicting",
            "samples": len(df)
        }
