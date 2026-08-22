from __future__ import annotations

import pandas as pd
from auth import service_client

TARGET_TABLE = "player_outcome_projection_engine"


def build():
    sb = service_client()

    df = pd.DataFrame(
        sb.table("player_outcome_forecast_engine").select("*").execute().data or []
    )

    if df.empty:
        print("No forecast data")
        return

    df["sleeper_id"] = df["sleeper_id"].astype(str)
    df["owner_team_name"] = df["owner_team_name"].fillna("UNROSTERED")

    rows = []

    for (sid, owner), g in df.groupby(["sleeper_id", "owner_team_name"]):

        g = g.sort_values("forecast_year")

        p1 = g.iloc[0]["projected_value_score"]
        p3 = g.iloc[2]["projected_value_score"] if len(g) > 2 else p1

        verdict = "WIN-NOW" if p1 > p3 else "LONG-TERM"

        rows.append({
            "sleeper_id": sid,
            "owner_team_name": owner,
            "player_name": g.iloc[0]["player_name"],
            "pos": g.iloc[0]["pos"],

            "projection_1yr": p1,
            "projection_2yr": g.iloc[1]["projected_value_score"] if len(g) > 1 else p1,
            "projection_3yr": p3,
            "projection_4yr": None,
            "projection_5yr": None,

            "peak_window": "Years 1-2",
            "decline_window": "Year 3+",
            "retirement_window": "TBD",

            "best_use_case": verdict,
            "outcome_verdict": verdict,

            "projection_summary": f"{g.iloc[0]['player_name']} is {verdict}"
        })

    out = pd.DataFrame(rows)

    sb.table(TARGET_TABLE).upsert(
        out.to_dict("records"),
        on_conflict="sleeper_id,owner_team_name"
    ).execute()

    print(f"Upserted {len(out)} projection rows")


if __name__ == "__main__":
    build()
