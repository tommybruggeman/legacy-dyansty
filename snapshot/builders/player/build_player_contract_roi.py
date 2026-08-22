from __future__ import annotations

import numpy as np
import pandas as pd

from auth import service_client
from contract_engine.internal_reads import load_internal_contract_rows

TARGET_TABLE = "player_contract_roi"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def safe_num(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def recommendation(row):
    roi = row["contract_roi_score"]
    value = row["football_value_score"]
    cost = row["contract_cost_score"]
    salary = row["salary"]
    years = row["years"]
    drop = row["drop_consideration_score"]
    risk = row["overpay_risk_score"]

    if value >= 85 and roi >= 45:
        return "CORE HOLD"

    if value >= 80 and cost >= 75:
        return "EXPENSIVE CORE HOLD"

    if value >= 70 and risk >= 65:
        return "SHOP / RESTRUCTURE"

    if roi >= 65:
        return "VALUE CONTRACT / HOLD"

    if value >= 60 and roi >= 40:
        return "HOLD"

    if drop >= 78 and value < 45:
        return "DROP / DEAD CAP REVIEW"

    if salary >= 25 and roi < 35:
        return "SHOP CONTRACT"

    if years >= 3 and roi < 40:
        return "CONTRACT RISK"

    return "HOLD / MONITOR"


def reason(row):
    return (
        f"{row['recommendation']}: "
        f"football value {row['football_value_score']}, "
        f"contract cost {row['contract_cost_score']}, "
        f"ROI {row['contract_roi_score']}, "
        f"asset {row['dynasty_asset_score']}, "
        f"win-now {row['win_now_score']}, "
        f"situation {row['situation_score']}, "
        f"salary ${row['salary']}, "
        f"years {row['years']}, "
        f"overpay risk {row['overpay_risk_score']}, "
        f"drop score {row['drop_consideration_score']}."
    )


def build_player_contract_roi():
    sb = service_client()

    contracts = pd.DataFrame(load_internal_contract_rows(sb))

    engine = pd.DataFrame(
        sb.table("player_engine_scores").select("*").execute().data or []
    )

    situation = pd.DataFrame(
        sb.table("player_situation_context").select("*").execute().data or []
    )

    if contracts.empty:
        print("No contracts found.")
        return pd.DataFrame()

    contracts = contracts.rename(columns={
        "sleeper_player_id": "sleeper_id",
        "owner_name": "owner_team_name",
        "player_position": "pos",
        "contract_years_left": "years",
    })

    contracts["sleeper_id"] = contracts["sleeper_id"].astype(str)

    if not engine.empty:
        engine["sleeper_id"] = engine["sleeper_id"].astype(str)

    if not situation.empty:
        situation["sleeper_id"] = situation["sleeper_id"].astype(str)

    df = contracts.merge(
        engine,
        on="sleeper_id",
        how="left",
        suffixes=("", "_engine"),
    )

    if not situation.empty:
        df = df.merge(
            situation,
            on=["sleeper_id", "owner_team_name"],
            how="left",
            suffixes=("", "_situation"),
        )

    rows = []

    for _, r in df.iterrows():
        salary = safe_num(r.get("salary"))
        years = safe_num(r.get("years"))

        engine_score = clamp(r.get("engine_score"))
        base_score = clamp(r.get("base_player_score"))
        recent_score = clamp(r.get("recent_production_score"))
        career_score = clamp(r.get("career_score_scaled", r.get("career_score")))
        durability = clamp(r.get("durability_score"))
        age_curve = clamp(r.get("age_curve_score", 50))
        situation_score = clamp(r.get("situation_score", 50))
        situation_risk = clamp(r.get("situation_risk_score"))

        dynasty_asset = clamp(
            engine_score * 0.45
            + base_score * 0.30
            + career_score * 0.15
            + age_curve * 0.10
        )

        win_now = clamp(
            recent_score * 0.45
            + engine_score * 0.30
            + situation_score * 0.15
            + durability * 0.10
        )

        production = clamp(
            recent_score * 0.60
            + career_score * 0.25
            + durability * 0.15
        )

        football_value = clamp(
            dynasty_asset * 0.35
            + win_now * 0.25
            + situation_score * 0.15
            + production * 0.15
            + age_curve * 0.10
        )

        salary_cost = clamp(salary * 1.55)
        years_cost = clamp(max(0, years - 1) * 7.5)

        contract_cost = clamp(
            salary_cost
            + years_cost
            + situation_risk * 0.10
        )

        contract_roi = clamp(
            50
            + (football_value - contract_cost) * 0.75
        )

        overpay_risk = clamp(
            contract_cost
            - football_value * 0.55
            + max(0, salary - 25) * 1.15
            + max(0, years - 2) * 8
        )

        drop_consideration = clamp(
            overpay_risk * 0.45
            + max(0, 45 - football_value) * 1.15
            + max(0, salary - 18) * 0.85
            + max(0, years - 2) * 8
        )

        restructure_priority = clamp(
            overpay_risk * 0.55
            + max(0, salary - 20) * 1.25
            + football_value * 0.15
        )

        trade_contract_value = clamp(
            football_value * 0.65
            + contract_roi * 0.25
            - overpay_risk * 0.25
        )

        row = {
            "sleeper_id": str(r.get("sleeper_id")),
            "owner_team_name": r.get("owner_team_name"),
            "player_name": r.get("player_name"),
            "pos": r.get("pos"),
            "salary": round(salary, 2),
            "years": round(years, 2),
            "dynasty_asset_score": round(dynasty_asset, 2),
            "win_now_score": round(win_now, 2),
            "production_score": round(production, 2),
            "situation_score": round(situation_score, 2),
            "salary_efficiency_score": round(clamp(100 - contract_cost + football_value * 0.35), 2),
            "contract_roi_score": round(contract_roi, 2),
            "overpay_risk_score": round(overpay_risk, 2),
            "drop_consideration_score": round(drop_consideration, 2),
            "restructure_priority_score": round(restructure_priority, 2),
            "trade_contract_value_score": round(trade_contract_value, 2),
            "football_value_score": round(football_value, 2),
            "contract_cost_score": round(contract_cost, 2),
        }

        row["recommendation"] = recommendation(row)
        row["reason"] = reason(row)

        rows.append(row)

    out = pd.DataFrame(rows)

    out = out.dropna(subset=["sleeper_id", "owner_team_name"])
    out = out[out["sleeper_id"].astype(str).str.strip() != ""]
    out = out[out["owner_team_name"].astype(str).str.strip() != ""]

    out = out.sort_values(
        ["sleeper_id", "owner_team_name", "salary", "years"],
        ascending=[True, True, False, False],
    ).drop_duplicates(
        subset=["sleeper_id", "owner_team_name"],
        keep="first",
    )

    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    print(f"Prepared contract ROI rows: {len(rows)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} player_contract_roi rows.")

    return out


if __name__ == "__main__":
    df = build_player_contract_roi()

    if not df.empty:
        print(
            df[
                [
                    "player_name",
                    "owner_team_name",
                    "pos",
                    "salary",
                    "years",
                    "football_value_score",
                    "contract_cost_score",
                    "contract_roi_score",
                    "overpay_risk_score",
                    "drop_consideration_score",
                    "recommendation",
                ]
            ]
            .sort_values("overpay_risk_score", ascending=False)
            .head(35)
            .to_string(index=False)
        )
