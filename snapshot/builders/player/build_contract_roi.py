from __future__ import annotations

import pandas as pd

from auth import service_client


SOURCE_TABLE = "player_intelligence"
TARGET_TABLE = "player_contract_roi"


def _num(v, default=0.0) -> float:
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def load_player_intelligence() -> pd.DataFrame:
    sb = service_client()
    rows = sb.table(SOURCE_TABLE).select("*").execute().data or []
    return pd.DataFrame(rows)


def calculate_roi(row: dict) -> dict:
    salary = _num(row.get("salary", 0))
    years = _num(row.get("years", 0))

    engine = _num(row.get("engine_score", 50))
    production = _num(row.get("recent_production_score", engine))
    contract_value = _num(row.get("contract_value_score", 50))
    asset = _num(row.get("dynasty_asset_score", engine))

    total_contract_cost = salary * max(years, 1)

    blended_value = (
        engine * 0.35
        + production * 0.25
        + asset * 0.25
        + contract_value * 0.15
    )

    if salary <= 0:
        roi = blended_value
    else:
        roi = blended_value / salary * 10

    dead_cap_estimate = salary * years * 0.50

    return {
        "player_name": row.get("player_name"),
        "owner_team_name": row.get("owner_team_name"),
        "pos": row.get("pos"),
        "salary": salary,
        "years": years,
        "engine_score": engine,
        "recent_production_score": production,
        "contract_value_score": contract_value,
        "dynasty_asset_score": asset,
        "total_contract_cost": round(total_contract_cost, 2),
        "blended_value_score": round(blended_value, 2),
        "contract_roi_score": round(roi, 2),
        "dead_cap_estimate": round(dead_cap_estimate, 2),
    }


def build_contract_roi() -> pd.DataFrame:
    df = load_player_intelligence()

    if df.empty:
        print("No player intelligence rows found.")
        return pd.DataFrame()

    rows = [calculate_roi(r) for r in df.to_dict("records")]
    out = pd.DataFrame(rows)

    return out


def write_contract_roi(df: pd.DataFrame):
    if df.empty:
        print("No ROI rows to write.")
        return

    sb = service_client()
    rows = df.to_dict("records")

    sb.table(TARGET_TABLE).upsert(
        rows,
        on_conflict="player_name,owner_team_name"
    ).execute()

    print(f"Upserted {len(rows)} contract ROI rows.")


if __name__ == "__main__":
    roi = build_contract_roi()
    print(roi.head(20).to_string(index=False))
    write_contract_roi(roi)

