from __future__ import annotations

import pandas as pd


def build_player_season_stats_snapshot(ctx: dict) -> pd.DataFrame:
    stats = ctx.get("player_season_stats", pd.DataFrame()).copy()

    if stats.empty:
        return pd.DataFrame()

    keep_cols = [
        "season",
        "canonical_player_id",
        "sleeper_id",
        "gsis_id",
        "player_name",
        "pos",
        "team",
        "games",
        "fantasy_points",
        "fantasy_points_ppr",
        "fantasy_ppg",
        "fantasy_ppg_ppr",
        "overall_rank",
        "position_rank",
        "passing_yards",
        "passing_tds",
        "interceptions",
        "rushing_yards",
        "rushing_tds",
        "receptions",
        "receiving_yards",
        "receiving_tds",
    ]

    existing_cols = [c for c in keep_cols if c in stats.columns]
    df = stats[existing_cols].copy()

    df["sleeper_id"] = df["sleeper_id"].astype(str).str.strip()
    df["player_name"] = df["player_name"].astype(str).str.strip()
    df["pos"] = df["pos"].astype(str).str.upper().str.strip()

    for col in df.columns:
        if col not in ["canonical_player_id", "sleeper_id", "gsis_id", "player_name", "pos", "team"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values(
        ["season", "fantasy_points_ppr"],
        ascending=[False, False],
    ).reset_index(drop=True)
