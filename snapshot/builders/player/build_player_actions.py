from __future__ import annotations

import pandas as pd
import numpy as np

from auth import service_client


SOURCE_TABLE = "player_snapshot"
TARGET_TABLE = "player_actions"


def load_snapshot() -> pd.DataFrame:
    sb = service_client()
    rows = sb.table(SOURCE_TABLE).select("*").execute().data or []
    return pd.DataFrame(rows)


def _num(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def evaluate_action(row: dict) -> dict:
    salary = _num(row.get("salary"))
    years = _num(row.get("years"))
    roi = _num(row.get("contract_roi_score"))
    engine = _num(row.get("engine_score"), 50)
    production = _num(row.get("recent_production_score"), 0)
    trade_value = _num(row.get("trade_value_score"), 50)
    contract_value = _num(row.get("contract_value_score"), 50)
    dead_cap = _num(row.get("dead_cap_estimate"), 0)

    drop_score = (
        (100 - roi) * 0.35
        + salary * 0.85
        + years * 5
        + (55 - production) * 0.25
        - trade_value * 0.25
    )

    trade_score = (
        (100 - roi) * 0.25
        + trade_value * 0.45
        + salary * 0.45
        + years * 3
        - dead_cap * 0.15
    )

    hold_score = (
        roi * 0.35
        + engine * 0.35
        + production * 0.20
        + contract_value * 0.10
    )

    buy_score = (
        roi * 0.30
        + engine * 0.30
        + production * 0.20
        + contract_value * 0.20
        - salary * 0.20
    )

    # True drops should be reserved for bad assets with manageable dead cap.
    # Expensive name-brand players should be audited/shopped before eating dead cap.
    if drop_score >= 68 and dead_cap <= salary and trade_value < 45 and engine < 55:
        action = "DROP / EAT DEAD CAP"
        reason = "contract is underwater, dead-cap pain is manageable, and trade market value is weak"

    elif roi <= 15 and salary >= 20 and trade_value >= 50:
        action = "AUDIT / SHOP"
        reason = "contract is underwater, but market value is strong enough to explore trades before cutting"

    elif trade_score >= 62 and trade_value >= 50:
        action = "SHOP / TRADE"
        reason = "name value or market value is stronger than contract ROI"

    elif hold_score >= 62:
        action = "KEEP"
        reason = "player value and contract profile are strong enough to hold"

    elif buy_score >= 62:
        action = "BUY / TARGET"
        reason = "profile is attractive relative to contract cost"

    elif roi <= 15 and salary >= 20:
        action = "AUDIT"
        reason = "high salary with weak contract ROI; compare trade, cut, and replacement options"

    else:
        action = "HOLD / MONITOR"
        reason = "no urgent move, but continue monitoring role, production, and market value"

    return {
        "sleeper_id": row.get("sleeper_id"),
        "player_name": row.get("player_name"),
        "owner_team_name": row.get("owner_team_name"),
        "pos": row.get("pos"),
        "recommended_action": action,
        "action_reason": reason,
        "drop_score": round(max(drop_score, 0), 2),
        "trade_score": round(max(trade_score, 0), 2),
        "hold_score": round(max(hold_score, 0), 2),
        "buy_score": round(max(buy_score, 0), 2),
        "contract_roi_score": round(roi, 2),
        "dead_cap_estimate": round(dead_cap, 2),
        "salary": salary,
        "years": years,
        "engine_score": round(engine, 2),
        "recent_production_score": round(production, 2),
        "trade_value_score": round(trade_value, 2),
        "contract_value_score": round(contract_value, 2),
    }


def build_player_actions() -> pd.DataFrame:
    df = load_snapshot()

    if df.empty:
        print("No player_snapshot rows found.")
        return pd.DataFrame()

    rows = [evaluate_action(r) for r in df.to_dict("records")]
    out = pd.DataFrame(rows)

    print(f"Built action rows: {len(out)}")
    return out


def write_player_actions(df: pd.DataFrame):
    if df.empty:
        print("No action rows to write.")
        return

    clean = df.replace([np.inf, -np.inf], None)
    clean = clean.astype(object).where(pd.notnull(clean), None)
    rows = clean.to_dict("records")

    sb = service_client()
    sb.table(TARGET_TABLE).upsert(
        rows,
        on_conflict="sleeper_id,owner_team_name",
    ).execute()

    print(f"Upserted {len(rows)} player_actions rows.")


if __name__ == "__main__":
    actions = build_player_actions()
    print(
        actions.sort_values(
            ["drop_score", "trade_score"],
            ascending=[False, False],
        ).head(30).to_string(index=False)
    )
    write_player_actions(actions)
