from __future__ import annotations

import pandas as pd
import numpy as np

from auth import service_client


SOURCE_TABLE = "player_snapshot"
TARGET_TABLE = "player_action_brain"


def _num(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def load_snapshot() -> pd.DataFrame:
    sb = service_client()
    rows = sb.table(SOURCE_TABLE).select("*").execute().data or []
    return pd.DataFrame(rows)


def grade_contract(row: dict) -> tuple[str, float, str]:
    salary = _num(row.get("salary"))
    years = _num(row.get("years"))
    roi = _num(row.get("contract_roi_score"))
    dead_cap = _num(row.get("dead_cap_estimate"))

    score = (
        roi * 0.55
        + (100 - salary * 2) * 0.25
        + (100 - years * 12) * 0.10
        + (100 - dead_cap) * 0.10
    )

    if score >= 70:
        return "GOOD", round(score, 2), "contract creates surplus value"
    if score >= 45:
        return "NEUTRAL", round(score, 2), "contract is manageable"
    if dead_cap > salary:
        return "BAD_BUT_EXPENSIVE_TO_EXIT", round(score, 2), "contract is poor, but dead cap makes cutting painful"
    return "BAD", round(score, 2), "contract is poor and should be reviewed"


def grade_performance(row: dict) -> tuple[str, float, str]:
    engine = _num(row.get("engine_score"), 50)
    production = _num(row.get("recent_production_score"), 0)
    trend = _num(row.get("trend_score"), 50)
    durability = _num(row.get("durability_score"), 50)

    score = (
        engine * 0.35
        + production * 0.35
        + trend * 0.15
        + durability * 0.15
    )

    if score >= 70:
        return "STRONG", round(score, 2), "performance profile is strong"
    if score >= 50:
        return "STABLE", round(score, 2), "performance profile is usable"
    if score >= 35:
        return "WEAK", round(score, 2), "performance profile is below target"
    return "REPLACEMENT_LEVEL", round(score, 2), "performance profile is replacement-level"


def grade_market(row: dict) -> tuple[str, float, str]:
    trade = _num(row.get("trade_value_score"), 50)
    engine = _num(row.get("engine_score"), 50)
    salary = _num(row.get("salary"))

    score = (
        trade * 0.55
        + engine * 0.30
        - salary * 0.25
        + 20
    )

    if score >= 65:
        return "STRONG_MARKET", round(score, 2), "league market should still value the player"
    if score >= 45:
        return "TRADEABLE", round(score, 2), "player likely has some trade market"
    if score >= 30:
        return "THIN_MARKET", round(score, 2), "trade market may be limited"
    return "NO_MARKET", round(score, 2), "trade market likely weak"


def grade_future(row: dict) -> tuple[str, float, str]:
    age_curve = _num(row.get("age_curve_score"), 50)
    trend = _num(row.get("trend_score"), 50)
    durability = _num(row.get("durability_score"), 50)
    recent_games = _num(row.get("recent_games"), 0)

    score = (
        age_curve * 0.35
        + trend * 0.25
        + durability * 0.25
        + min(recent_games, 50) * 0.30
    )

    if score >= 70:
        return "ASCENDING_OR_SAFE", round(score, 2), "future profile looks strong"
    if score >= 50:
        return "STABLE_FUTURE", round(score, 2), "future profile is stable"
    if score >= 35:
        return "UNCERTAIN_FUTURE", round(score, 2), "future profile has meaningful uncertainty"
    return "DECLINE_RISK", round(score, 2), "future profile carries decline risk"


def decide(row: dict, contract, performance, market, future) -> tuple[str, str]:
    contract_grade, contract_score, _ = contract
    performance_grade, performance_score, _ = performance
    market_grade, market_score, _ = market
    future_grade, future_score, _ = future

    salary = _num(row.get("salary"))
    years = _num(row.get("years"))
    roi = _num(row.get("contract_roi_score"))
    dead_cap = _num(row.get("dead_cap_estimate"))
    engine = _num(row.get("engine_score"), 50)

    if (
        contract_grade in ["BAD", "BAD_BUT_EXPENSIVE_TO_EXIT"]
        and market_grade in ["STRONG_MARKET", "TRADEABLE"]
        and engine >= 55
    ):
        return (
            "AUDIT / SHOP",
            "contract is underwater, but player quality or market value means trade should be explored before any cut",
        )

    if (
        contract_grade == "BAD"
        and performance_grade in ["WEAK", "REPLACEMENT_LEVEL"]
        and market_grade in ["THIN_MARKET", "NO_MARKET"]
        and dead_cap <= salary
    ):
        return (
            "CUT CANDIDATE",
            "contract, performance, and market signals are all weak with manageable dead cap",
        )

    if (
        performance_grade == "STRONG"
        and future_grade in ["ASCENDING_OR_SAFE", "STABLE_FUTURE"]
        and contract_grade != "BAD"
    ):
        return (
            "KEEP",
            "performance and future outlook justify holding",
        )

    if (
        contract_grade == "GOOD"
        and performance_grade in ["STABLE", "STRONG"]
    ):
        return (
            "VALUE HOLD",
            "contract is efficient enough to keep unless included in a larger upgrade",
        )

    if (
        market_grade == "STRONG_MARKET"
        and contract_grade == "BAD_BUT_EXPENSIVE_TO_EXIT"
    ):
        return (
            "SHOP, DO NOT CUT",
            "bad contract, but dead cap makes cutting inefficient and market value should be tested first",
        )

    if roi <= 15 and salary >= 20:
        return (
            "AUDIT",
            "high-cost contract needs comparison against trade, replacement, and future outlook",
        )

    return (
        "HOLD / MONITOR",
        "no single signal is strong enough to force action yet",
    )


def evaluate_row(row: dict) -> dict:
    contract = grade_contract(row)
    performance = grade_performance(row)
    market = grade_market(row)
    future = grade_future(row)
    action, reason = decide(row, contract, performance, market, future)

    return {
        "sleeper_id": row.get("sleeper_id"),
        "player_name": row.get("player_name"),
        "owner_team_name": row.get("owner_team_name"),
        "pos": row.get("pos"),

        "recommended_action": action,
        "action_reason": reason,

        "contract_grade": contract[0],
        "contract_score": contract[1],
        "contract_reason": contract[2],

        "performance_grade": performance[0],
        "performance_score": performance[1],
        "performance_reason": performance[2],

        "market_grade": market[0],
        "market_score": market[1],
        "market_reason": market[2],

        "future_grade": future[0],
        "future_score": future[1],
        "future_reason": future[2],

        "future_production_score": _num(row.get("future_production_score"), 50),
        "future_value_score": _num(row.get("future_value_score"), 50),
        "projection_tier": row.get("projection_tier"),
        "future_sample_confidence": _num(row.get("sample_confidence"), 0),
        "is_free_agent": bool(row.get("is_free_agent", False)),

        "salary": _num(row.get("salary")),
        "years": _num(row.get("years")),
        "contract_roi_score": _num(row.get("contract_roi_score")),
        "dead_cap_estimate": _num(row.get("dead_cap_estimate")),
        "engine_score": _num(row.get("engine_score"), 50),
        "recent_production_score": _num(row.get("recent_production_score"), 0),
        "trade_value_score": _num(row.get("trade_value_score"), 50),
    }



def load_future_projection() -> pd.DataFrame:
    sb = service_client()

    print("Loading player future projections...")
    future = pd.DataFrame(
        sb.table("player_future_projection").select("*").execute().data or []
    )

    if future.empty:
        print("No player_future_projection rows found.")
        return future

    future["sleeper_id"] = future["sleeper_id"].astype(str)
    return future


def build_action_brain() -> pd.DataFrame:
    df = load_snapshot()

    if df.empty:
        print("No player_snapshot rows found.")
        return pd.DataFrame()

    future = load_future_projection()

    if not future.empty and "sleeper_id" in df.columns:
        df["sleeper_id"] = df["sleeper_id"].astype(str)

        keep_cols = [
            c for c in [
                "sleeper_id",
                "future_production_score",
                "future_value_score",
                "projection_tier",
                "sample_confidence",
                "is_free_agent",
            ]
            if c in future.columns
        ]

        df = df.merge(
            future[keep_cols],
            on="sleeper_id",
            how="left",
        )

    out = pd.DataFrame([evaluate_row(r) for r in df.to_dict("records")])
    print(f"Built action brain rows: {len(out)}")
    return out


def write_action_brain(df: pd.DataFrame):
    if df.empty:
        print("No rows to write.")
        return

    clean = df.replace([np.inf, -np.inf], None)
    clean = clean.astype(object).where(pd.notnull(clean), None)

    sb = service_client()
    sb.table(TARGET_TABLE).upsert(
        clean.to_dict("records"),
        on_conflict="sleeper_id,owner_team_name",
    ).execute()

    print(f"Upserted {len(df)} player_action_brain rows.")


if __name__ == "__main__":
    df = build_action_brain()
    print(df.sort_values(["contract_score", "market_score"], ascending=[True, False]).head(35).to_string(index=False))
    write_action_brain(df)
