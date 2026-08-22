from __future__ import annotations

import pandas as pd


def build_player_rankings_snapshot(ctx: dict) -> pd.DataFrame:
    rankings = ctx.get("player_rankings", pd.DataFrame()).copy()

    if rankings.empty:
        return pd.DataFrame()

    keep_cols = [
        "sleeper_id",
        "player",
        "pos",
        "dynasty_rank",
        "position_rank",
        "tier",
        "base_player_score",
        "source",
        "updated_at",
    ]

    existing_cols = [c for c in keep_cols if c in rankings.columns]
    df = rankings[existing_cols].copy()

    df["sleeper_id"] = df["sleeper_id"].astype(str).str.strip()
    df["player"] = df["player"].astype(str).str.strip()
    df["pos"] = df["pos"].astype(str).str.upper().str.strip()

    for col in ["dynasty_rank", "position_rank", "tier", "base_player_score"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values("dynasty_rank").reset_index(drop=True)
