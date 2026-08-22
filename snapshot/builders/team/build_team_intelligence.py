from __future__ import annotations

import pandas as pd
from auth import service_client


TARGET_TABLE = "team_intelligence"


def load_table(sb, table):
    return pd.DataFrame(sb.table(table).select("*").execute().data or [])


def main():
    sb = service_client()

    print("Loading team intelligence inputs...")

    window = load_table(sb, "team_window_scores")
    league = load_table(sb, "league_intelligence")

    print("team_window_scores:", len(window))
    print("league_intelligence:", len(league))

    if window.empty:
        print("No team_window_scores found.")
        return

    df = window.copy()

    if not league.empty:
        keep = [
            "owner_team_name",
            "overall_rank",
            "window_percentile",
            "future_percentile",
            "cap_percentile",
            "depth_percentile",
            "qb_rank",
            "rb_rank",
            "wr_rank",
            "te_rank",
            "league_window_label",
            "league_timeline_label",
            "strengths",
            "weaknesses",
            "trade_strategy",
        ]

        df = df.merge(
            league[[c for c in keep if c in league.columns]],
            on="owner_team_name",
            how="left",
        )

    # Normalize league_id if missing
    if "league_id" in df.columns:
        df["league_id"] = df["league_id"].fillna("9838a0a1-97c6-4cab-bb88-af177317abfe")
    else:
        df["league_id"] = "9838a0a1-97c6-4cab-bb88-af177317abfe"

    # Drop source table IDs so they do not collide with target schema
    if "id" in df.columns:
        df = df.drop(columns=["id"])

    import math
    import numpy as np

    df = df.replace([np.inf, -np.inf], np.nan)

    rows = []
    for row in df.to_dict("records"):
        clean = {}
        for k, v in row.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, (dict, list)):
                clean[k] = v
            elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                clean[k] = None
            elif pd.isna(v):
                clean[k] = None
            else:
                clean[k] = v
        rows.append(clean)

    sb.table(TARGET_TABLE).upsert(
        rows,
        on_conflict="owner_team_name",
    ).execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}.")


if __name__ == "__main__":
    main()
