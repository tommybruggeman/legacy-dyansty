from __future__ import annotations

import pandas as pd


def build_player_career_features_snapshot(ctx: dict) -> pd.DataFrame:
    features = ctx.get("player_career_features", pd.DataFrame()).copy()

    if features.empty:
        return pd.DataFrame()

    df = features.copy()

    for col in ["sleeper_id", "player_name", "pos"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    if "pos" in df.columns:
        df["pos"] = df["pos"].str.upper()

    numeric_exclude = {"sleeper_id", "player_name", "pos"}

    for col in df.columns:
        if col not in numeric_exclude:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values(
        ["latest_season", "career_fantasy_points_ppr"],
        ascending=[False, False],
    ).reset_index(drop=True)
