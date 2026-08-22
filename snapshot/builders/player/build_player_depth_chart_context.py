from __future__ import annotations

import pandas as pd
import numpy as np

from auth import service_client


TARGET_TABLE = "player_depth_chart_context"


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, float(v)))


def grade(score: float) -> str:
    if score >= 80:
        return "LOCKED_STARTER"
    if score >= 65:
        return "LIKELY_STARTER"
    if score >= 50:
        return "ROLE_PLAYER"
    if score >= 35:
        return "DEPTH_COMPETITION"
    return "LOW_DEPTH_SECURITY"


def load_table(sb, table: str) -> pd.DataFrame:
    print(f"Loading {table}...")
    return pd.DataFrame(sb.table(table).select("*").execute().data or [])


def build_depth_chart_context():
    sb = service_client()

    action = load_table(sb, "player_action_brain")
    forecast = load_table(sb, "player_forecast_brain")
    situation = load_table(sb, "player_situation_context")

    if action.empty:
        print("No action rows found.")
        return

    for df in [action, forecast, situation]:
        if not df.empty and "sleeper_id" in df.columns:
            df["sleeper_id"] = df["sleeper_id"].astype(str)

    df = action.copy()
    df["sleeper_id"] = df["sleeper_id"].astype(str)

    if not forecast.empty:
        df = df.merge(
            forecast[
                [
                    "sleeper_id",
                    "owner_team_name",
                    "forecast_score",
                    "forecast_confidence",
                    "ceiling_score",
                    "floor_score",
                    "bust_probability",
                    "breakout_probability",
                ]
            ],
            on=["sleeper_id", "owner_team_name"],
            how="left",
        )

    if not situation.empty:
        df = df.merge(
            situation[
                [
                    "sleeper_id",
                    "owner_team_name",
                    "role_security_score",
                    "situation_score",
                    "situation_risk_score",
                ]
            ],
            on=["sleeper_id", "owner_team_name"],
            how="left",
        )

    # Try to use NFL team if available. If missing, unknown-team groups still work.
    if "nfl_team" not in df.columns:
        df["nfl_team"] = "UNK"

    df["nfl_team"] = df["nfl_team"].fillna("UNK")

    numeric_cols = [
        "performance_score",
        "market_score",
        "future_score",
        "future_value_score",
        "forecast_score",
        "forecast_confidence",
        "ceiling_score",
        "floor_score",
        "bust_probability",
        "breakout_probability",
        "role_security_score",
        "situation_score",
        "situation_risk_score",
        "salary",
        "years",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["depth_score_base"] = (
        df.get("forecast_score", 50).fillna(50) * 0.30
        + df.get("performance_score", 50).fillna(50) * 0.22
        + df.get("market_score", 50).fillna(50) * 0.18
        + df.get("role_security_score", 50).fillna(50) * 0.15
        + df.get("ceiling_score", 50).fillna(50) * 0.10
        - df.get("bust_probability", 50).fillna(50) * 0.05
    ).clip(0, 100)

    group_cols = ["nfl_team", "pos"]

    df["nfl_team_pos_count"] = df.groupby(group_cols)["sleeper_id"].transform("count")

    df["depth_chart_rank"] = (
        df.groupby(group_cols)["depth_score_base"]
        .rank(method="first", ascending=False)
        .astype(int)
    )

    df["team_pos_best_score"] = df.groupby(group_cols)["depth_score_base"].transform("max")
    df["team_pos_next_score"] = 0.0

    for _, idxs in df.groupby(group_cols).groups.items():
        idxs = list(idxs)
        sub = df.loc[idxs].sort_values("depth_score_base", ascending=False)
        scores = sub["depth_score_base"].tolist()

        for i, idx in enumerate(sub.index):
            others = scores[:i] + scores[i + 1:]
            df.loc[idx, "team_pos_next_score"] = max(others) if others else 0.0

    rows = []

    for _, r in df.iterrows():
        rank = int(r.get("depth_chart_rank") or 99)
        count = int(r.get("nfl_team_pos_count") or 1)

        base = float(r.get("depth_score_base") or 50)
        next_score = float(r.get("team_pos_next_score") or 0)

        performance = float(r.get("performance_score") or 50)
        market = float(r.get("market_score") or 50)
        forecast = float(r.get("forecast_score") or 50)
        confidence = float(r.get("forecast_confidence") or 50)
        role_security = float(r.get("role_security_score") or 50)
        bust = float(r.get("bust_probability") or 50)

        gap_to_competition = base - next_score

        competition_score = clamp(
            max(0, count - 1) * 10
            + max(0, 12 - gap_to_competition) * 2.0
            + max(0, rank - 1) * 14
        )

        starter_probability = clamp(
            forecast * 0.30
            + performance * 0.22
            + market * 0.18
            + role_security * 0.18
            + confidence * 0.12
            - competition_score * 0.25
            - bust * 0.08
        )

        role_security_adjusted = clamp(
            role_security * 0.45
            + starter_probability * 0.35
            + max(0, 100 - competition_score) * 0.20
        )

        if r.get("pos") == "QB" and rank > 1 and starter_probability < 55:
            flag = "QB_COMPETITION_RISK"
        elif competition_score >= 70:
            flag = "DEPTH_CHART_CROWDING"
        elif starter_probability < 35:
            flag = "LOW_STARTER_PROBABILITY"
        else:
            flag = None

        note = (
            f"Machine-derived from NFL team/position group rank {rank}/{count}, "
            f"forecast, performance, market, role security, confidence, and bust risk."
        )

        rows.append({
            "sleeper_id": str(r.get("sleeper_id")),
            "owner_team_name": r.get("owner_team_name"),
            "player_name": r.get("player_name"),
            "pos": r.get("pos"),
            "nfl_team": r.get("nfl_team"),

            "nfl_team_pos_count": count,
            "depth_chart_rank": rank,
            "depth_score_base": round(base, 2),
            "team_pos_next_score": round(next_score, 2),
            "gap_to_competition": round(gap_to_competition, 2),

            "competition_score": round(competition_score, 2),
            "starter_probability": round(starter_probability, 2),
            "role_security_adjusted": round(role_security_adjusted, 2),

            "depth_chart_grade": grade(role_security_adjusted),
            "depth_chart_risk_flag": flag,
            "depth_chart_note": note,
        })

    print(f"Prepared depth chart context rows: {len(rows)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} player_depth_chart_context rows.")


if __name__ == "__main__":
    build_depth_chart_context()
