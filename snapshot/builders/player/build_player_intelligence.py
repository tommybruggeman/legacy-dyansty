from __future__ import annotations

import pandas as pd

from auth import service_client


TARGET_TABLE = "player_intelligence"


def load_table(sb, table):
    rows = sb.table(table).select("*").execute().data or []
    return pd.DataFrame(rows)


def main():

    sb = service_client()

    print("Loading engine tables...")

    rankings = load_table(sb, "player_rankings")
    engine = load_table(sb, "player_engine_scores")
    values = load_table(sb, "player_values")

    print(len(rankings), "rankings")
    print(len(engine), "engine rows")
    print(len(values), "value rows")

    # -------------------------------------------------
    # Start with engine table
    # -------------------------------------------------

    df = engine.copy()

    # -------------------------------------------------
    # Merge rankings
    # -------------------------------------------------

    if not rankings.empty:

        keep = [
            "sleeper_id",
            "dynasty_rank",
            "position_rank",
            "tier",
        ]

        df = df.merge(
            rankings[keep],
            on="sleeper_id",
            how="left",
        )

    # -------------------------------------------------
    # Merge player values
    # -------------------------------------------------

    if not values.empty:

        values = values.rename(
            columns={
                "player_id": "sleeper_id"
            }
        )

        keep = [
            "sleeper_id",
            "trade_value_score",
            "contract_value_score",
            "roster_fit_score",
            "positional_need_score",
        ]

        df = df.merge(
            values[[c for c in keep if c in values.columns]],
            on="sleeper_id",
            how="left",
        )

    print(df.head())

    print("Rows:", len(df))

    # -------------------------------------------------
    # Write player intelligence
    # -------------------------------------------------

    if df.empty:
        print("No player intelligence rows to write.")
        return

    import math
    import numpy as np

    df = df.replace([np.inf, -np.inf], np.nan)

    raw_rows = df.to_dict("records")
    rows = []

    for row in raw_rows:
        clean = {}
        for k, v in row.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean[k] = None
            elif pd.isna(v):
                clean[k] = None
            else:
                clean[k] = v
        rows.append(clean)

    sb.table(TARGET_TABLE).upsert(
        rows,
        on_conflict="sleeper_id",
    ).execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}.")


if __name__ == "__main__":
    main()
