
import pandas as pd
import numpy as np

class FeatureLearningEngine:

    def analyze(self, df: pd.DataFrame):

        if df.empty or "error" not in df.columns:
            return {"status": "insufficient_data"}

        results = {}

        # -----------------------------
        # CORE FEATURE CORRELATIONS
        # -----------------------------

        feature_cols = [
            "context_score",
            "volatility"
        ]

        for col in feature_cols:
            if col in df.columns:
                corr = df[col].corr(df["error"])
                results[col] = float(corr) if not pd.isna(corr) else 0.0

        # -----------------------------
        # SYSTEM BIAS DETECTION
        # -----------------------------

        results["system_bias"] = {
            "overpredicting_high_context": bool(results.get("context_score", 0) < -0.2),
            "volatility_miscalibration": bool(abs(results.get("volatility", 0)) > 0.2)
        }

        # -----------------------------
        # FEATURE PRIORITY SIGNAL
        # -----------------------------

        ranked = sorted(results.items(), key=lambda x: abs(x[1]) if isinstance(x[1], (int, float)) else 0, reverse=True)

        results["feature_priority"] = [r[0] for r in ranked if isinstance(r[1], (int, float))]

        return results
