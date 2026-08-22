from __future__ import annotations

import numpy as np
import pandas as pd
from auth import service_client

TARGET_TABLE = "player_outcome_forecast_engine"


def clamp(x):
    try:
        if pd.isna(x):
            return 0.0
        return float(max(0, min(100, float(x))))
    except Exception:
        return 0.0


def build():
    sb = service_client()

    contract = pd.DataFrame(
        sb.table("player_intelligence_base").select("*").execute().data or []
    )

    if contract.empty:
        print("No base data")
        return

    contract["sleeper_id"] = contract["sleeper_id"].astype(str)
    contract["owner_team_name"] = contract["owner_team_name"].fillna("UNROSTERED")

    rows = []

    for _, r in contract.iterrows():

        age = clamp(r.get("age", 27))
        pos = r.get("pos")
        base = clamp(r.get("contract_salary"))

        for year in range(1, 6):

            decay = year * 3
            retire = max(0, (age + year - 30) * 5)

            value = clamp(70 - decay - retire * 0.5)

            rows.append({
                "sleeper_id": r["sleeper_id"],
                "owner_team_name": r["owner_team_name"],
                "player_name": r.get("player_name"),
                "pos": pos,

                "forecast_year": year,
                "years_out": year,

                "age": age + year,

                "projected_value_score": value,
                "projected_market_score": value * 0.9,
                "projected_production_score": value * 1.1,

                "projected_contract_pressure": base,

                "projected_decay_score": decay,
                "projected_injury_risk": retire,
                "projected_retirement_risk": retire,

                "win_now_value": value,
                "future_value": value,

                "contract_advice": "HOLD",
                "forecast_summary": f"{r.get('player_name')} year {year}: {value:.1f}"
            })

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(["sleeper_id", "owner_team_name", "forecast_year"])

    sb.table(TARGET_TABLE).upsert(
        df.to_dict("records"),
        on_conflict="sleeper_id,owner_team_name,forecast_year"
    ).execute()

    print(f"Upserted {len(df)} forecast rows")


if __name__ == "__main__":
    build()
