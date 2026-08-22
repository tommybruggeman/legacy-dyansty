from __future__ import annotations

import pandas as pd
import numpy as np

from auth import service_client


TARGET_TABLE = "player_forecast_brain"


def _num(v, default=0.0):
    try:
        x = pd.to_numeric(v, errors="coerce")
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, float(v)))


def grade(score: float) -> str:
    if score >= 85:
        return "ELITE_FORECAST"
    if score >= 72:
        return "STRONG_FORECAST"
    if score >= 58:
        return "STABLE_FORECAST"
    if score >= 45:
        return "VOLATILE_FORECAST"
    if score >= 32:
        return "HIGH_RISK_FORECAST"
    return "LOW_CONFIDENCE_AVOID"


def trajectory(score: float, situation: float, market: float, contract: float) -> str:
    if score >= 75 and situation >= 60:
        return "ASCENDING"
    if score >= 65 and market >= 60:
        return "STABLE_PLUS"
    if score >= 55:
        return "STABLE"
    if contract < 35 and score < 55:
        return "CONTRACT_DRAG"
    if situation < 40:
        return "SITUATION_RISK"
    return "DECLINING_OR_UNCERTAIN"


def recommendation(row: dict) -> tuple[str, str]:
    score = _num(row.get("forecast_score"), 50)
    contract = _num(row.get("contract_score"), 50)
    situation = _num(row.get("situation_score"), 50)
    market = _num(row.get("market_score"), 50)
    confidence = _num(row.get("forecast_confidence"), 50)
    salary = _num(row.get("salary"), 0)
    risk_flag = row.get("situation_risk_flag")

    if risk_flag in ["QB_BATTLE", "STARTING_JOB_RISK"] and salary >= 15:
        return (
            "SHOP / HEDGE",
            "role security risk plus meaningful salary makes this player dangerous to hold blindly",
        )

    if score >= 75 and contract >= 45:
        return (
            "BUILD AROUND",
            "forecast, situation, and contract are strong enough to treat as a core asset",
        )

    if score >= 68 and market >= 60:
        return (
            "HOLD / PRICE HIGH",
            "strong forecast and market value justify holding unless someone overpays",
        )

    if contract < 35 and market >= 55:
        return (
            "SHOP",
            "contract drag is meaningful, but market value may still create an exit",
        )

    if score < 45 and salary >= 10:
        return (
            "EXIT CANDIDATE",
            "forecast does not justify the contract unless team context changes",
        )

    if confidence < 35:
        return (
            "MONITOR",
            "forecast confidence is low, so avoid forcing a move without more context",
        )

    return (
        "HOLD / MONITOR",
        "overall forecast is not extreme enough to force a move",
    )


def load_table(sb, table: str) -> pd.DataFrame:
    print(f"Loading {table}...")
    return pd.DataFrame(sb.table(table).select("*").execute().data or [])


def build_forecast_brain():
    sb = service_client()

    action = load_table(sb, "player_action_brain")
    future = load_table(sb, "player_future_projection")
    situation = load_table(sb, "player_situation_context")

    if action.empty:
        print("No player_action_brain rows found.")
        return

    for df in [action, future, situation]:
        if not df.empty and "sleeper_id" in df.columns:
            df["sleeper_id"] = df["sleeper_id"].astype(str)

    df = action.copy()

    if not future.empty:
        keep = [
            c for c in [
                "sleeper_id",
                "future_production_score",
                "future_value_score",
                "projection_tier",
                "sample_confidence",
            ]
            if c in future.columns
        ]

        df = df.merge(
            future[keep],
            on="sleeper_id",
            how="left",
            suffixes=("", "_future"),
        )

    if not situation.empty:
        keep = [
            c for c in [
                "sleeper_id",
                "owner_team_name",
                "role_security_score",
                "depth_chart_pressure_score",
                "team_environment_score",
                "scheme_fit_score",
                "qb_environment_score",
                "offensive_line_score",
                "situation_risk_score",
                "situation_score",
                "situation_grade",
                "situation_risk_flag",
                "situation_note",
            ]
            if c in situation.columns
        ]

        df = df.merge(
            situation[keep],
            on=["sleeper_id", "owner_team_name"],
            how="left",
            suffixes=("", "_situation"),
        )

    rows = []

    for _, r in df.iterrows():
        future_value = _num(
            r.get("future_value_score"),
            _num(r.get("future_score"), 50),
        )
        future_prod = _num(
            r.get("future_production_score"),
            _num(r.get("recent_production_score"), 50),
        )

        situation_score = _num(r.get("situation_score"), 50)
        role_security = _num(r.get("role_security_score"), 50)
        situation_risk = _num(r.get("situation_risk_score"), 50)

        contract = _num(r.get("contract_score"), 50)
        market = _num(r.get("market_score"), 50)
        performance = _num(r.get("performance_score"), 50)
        confidence = _num(r.get("sample_confidence"), _num(r.get("future_sample_confidence"), 50))

        contract_drag = clamp(50 - contract)
        risk_drag = clamp(situation_risk * 0.25)

        one_year = clamp(
            performance * 0.35
            + future_prod * 0.25
            + situation_score * 0.20
            + role_security * 0.10
            + market * 0.10
            - risk_drag
        )

        two_year = clamp(
            future_value * 0.35
            + situation_score * 0.20
            + market * 0.20
            + performance * 0.15
            + contract * 0.10
            - contract_drag * 0.15
        )

        three_year = clamp(
            future_value * 0.40
            + market * 0.20
            + situation_score * 0.15
            + contract * 0.10
            + role_security * 0.15
            - contract_drag * 0.20
        )

        ceiling = clamp(
            max(one_year, two_year, three_year)
            + max(0, market - 60) * 0.20
            + max(0, future_value - 65) * 0.25
        )

        floor = clamp(
            min(one_year, two_year, three_year)
            - situation_risk * 0.20
            - contract_drag * 0.15
        )

        forecast_score = clamp(
            one_year * 0.35
            + two_year * 0.35
            + three_year * 0.20
            + ceiling * 0.05
            + floor * 0.05
        )

        forecast_confidence = clamp(
            confidence * 0.35
            + role_security * 0.25
            + market * 0.20
            + max(0, 100 - situation_risk) * 0.20
        )

        bust_probability = clamp(
            100 - floor
            + situation_risk * 0.20
            + contract_drag * 0.15
            - forecast_confidence * 0.20
        )

        breakout_probability = clamp(
            ceiling * 0.45
            + future_value * 0.30
            + situation_score * 0.15
            + market * 0.10
            - situation_risk * 0.20
        )

        rec, reason = recommendation({
            **r.to_dict(),
            "forecast_score": forecast_score,
            "forecast_confidence": forecast_confidence,
        })

        forecast_grade = grade(forecast_score)
        forecast_trajectory = trajectory(
            forecast_score,
            situation_score,
            market,
            contract,
        )

        rows.append({
            "sleeper_id": str(r.get("sleeper_id")),
            "owner_team_name": r.get("owner_team_name"),
            "player_name": r.get("player_name"),
            "pos": r.get("pos"),

            "one_year_score": round(one_year, 2),
            "two_year_score": round(two_year, 2),
            "three_year_score": round(three_year, 2),
            "ceiling_score": round(ceiling, 2),
            "floor_score": round(floor, 2),

            "forecast_score": round(forecast_score, 2),
            "forecast_grade": forecast_grade,
            "forecast_trajectory": forecast_trajectory,
            "forecast_confidence": round(forecast_confidence, 2),

            "bust_probability": round(bust_probability, 2),
            "breakout_probability": round(breakout_probability, 2),

            "recommended_forecast_action": rec,
            "forecast_reason": reason,

            "future_value_score": round(future_value, 2),
            "future_production_score": round(future_prod, 2),
            "situation_score": round(situation_score, 2),
            "situation_risk_score": round(situation_risk, 2),
            "role_security_score": round(role_security, 2),
            "contract_score": round(contract, 2),
            "market_score": round(market, 2),
            "performance_score": round(performance, 2),

            "projection_tier": r.get("projection_tier"),
            "situation_grade": r.get("situation_grade"),
            "situation_risk_flag": r.get("situation_risk_flag"),
            "situation_note": r.get("situation_note"),
        })

    print(f"Prepared forecast brain rows: {len(rows)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} player_forecast_brain rows.")


if __name__ == "__main__":
    build_forecast_brain()
