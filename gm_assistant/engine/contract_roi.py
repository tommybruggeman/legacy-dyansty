from __future__ import annotations

import pandas as pd


def _num(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def calculate_contract_roi(row: dict) -> dict:
    salary = _num(row.get("salary", 0))
    years = _num(row.get("years", 0))

    win_now = _num(row.get("win_now_score", 0))
    asset = _num(row.get("dynasty_asset_score", 0))
    production = _num(row.get("production_score", win_now))

    contract_cost = salary * max(years, 1)

    blended_value = (
        win_now * 0.40
        + asset * 0.35
        + production * 0.25
    )

    if salary <= 0:
        roi_score = blended_value
    else:
        roi_score = blended_value / salary * 10

    dead_cap_estimate = round((salary * years) * 0.50, 2)

    return {
        "salary_num": salary,
        "years_num": years,
        "contract_cost": round(contract_cost, 2),
        "blended_value": round(blended_value, 2),
        "contract_roi_score": round(roi_score, 2),
        "dead_cap_estimate": dead_cap_estimate,
    }


def add_contract_roi(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    roi_rows = out.apply(lambda r: calculate_contract_roi(r.to_dict()), axis=1)
    roi_df = pd.DataFrame(list(roi_rows))

    return pd.concat([out.reset_index(drop=True), roi_df.reset_index(drop=True)], axis=1)
