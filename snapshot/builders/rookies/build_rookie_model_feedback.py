from __future__ import annotations

import math
import pandas as pd
from auth import service_client

TARGET_TABLE = "rookie_model_feedback"


def safe_num(v, default=0.0):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except Exception:
        return default


def feedback_bucket(error: float, outcome_score: float, season_ppg: float) -> str:
    if season_ppg <= 0 and outcome_score < 25:
        return "PENDING NFL DATA"

    abs_error = abs(error)

    if abs_error <= 8:
        return "ACCURATE"
    if abs_error <= 18:
        return "MINOR MISS"
    if abs_error <= 32:
        return "MAJOR MISS"
    return "MODEL FAILURE"


def error_direction(error: float, outcome_score: float, season_ppg: float) -> str:
    if season_ppg <= 0 and outcome_score < 25:
        return "PENDING"

    if error > 8:
        return "MODEL OVERRATED"
    if error < -8:
        return "MODEL UNDERRATED"
    return "MODEL ACCURATE"


def build_summary(r: dict) -> str:
    return (
        f"{r['player_name']} feedback: {r['feedback_bucket']} / "
        f"{r['error_direction']}. "
        f"Model predicted {r['model_prediction_score']}; "
        f"actual outcome {r['outcome_score']}; "
        f"error {r['prediction_error']}. "
        f"Outcome bucket: {r['outcome_bucket']}."
    )


def build_rookie_model_feedback() -> pd.DataFrame:
    sb = service_client()

    rows = (
        sb.table("rookie_draft_outcomes")
        .select("*")
        .execute()
        .data
        or []
    )

    if not rows:
        print("No rookie_draft_outcomes rows found.")
        return pd.DataFrame()

    out = []

    for r in rows:
        draft_grade = safe_num(r.get("draft_grade"), 50)
        projected_value = safe_num(r.get("projected_value"), draft_grade)

        # First-pass model prediction:
        # projected_value is usually the best available pre-outcome estimate.
        # draft_grade acts as stabilizer if projected_value is missing/noisy.
        model_prediction_score = round((projected_value * 0.70) + (draft_grade * 0.30), 1)

        outcome_score = safe_num(r.get("outcome_score"), 0)
        prediction_error = round(model_prediction_score - outcome_score, 1)
        season_ppg = safe_num(r.get("season_ppg"), 0)

        row = {
            "class_year": r.get("class_year") or r.get("rookie_class_year") or r.get("draft_year"),
            "draft_year": r.get("draft_year") or r.get("class_year"),
            "rookie_class_year": r.get("rookie_class_year") or r.get("class_year"),
            "sleeper_id": r.get("sleeper_id"),
            "player_name": r.get("player_name"),
            "pos": r.get("pos"),

            "rookie_rank": r.get("rookie_rank"),
            "draft_grade": draft_grade,
            "projected_value": projected_value,
            "model_prediction_score": model_prediction_score,

            "season_ppg": season_ppg,
            "salary": safe_num(r.get("salary"), 0),
            "years": safe_num(r.get("years"), 0),
            "outcome_score": outcome_score,
            "outcome_bucket": r.get("outcome_bucket"),

            "prediction_error": prediction_error,
            "error_direction": error_direction(prediction_error, outcome_score, season_ppg),
            "feedback_bucket": feedback_bucket(prediction_error, outcome_score, season_ppg),
        }

        row["feedback_summary"] = build_summary(row)
        out.append(row)

    df = pd.DataFrame(out)

    sb.table(TARGET_TABLE).upsert(
        df.to_dict("records"),
        on_conflict="class_year,player_name",
    ).execute()

    print(f"Upserted {len(df)} rookie_model_feedback rows")
    print(
        df[
            [
                "class_year",
                "rookie_rank",
                "player_name",
                "pos",
                "model_prediction_score",
                "outcome_score",
                "prediction_error",
                "error_direction",
                "feedback_bucket",
            ]
        ]
        .sort_values("rookie_rank")
        .to_string(index=False)
    )

    return df


if __name__ == "__main__":
    build_rookie_model_feedback()
