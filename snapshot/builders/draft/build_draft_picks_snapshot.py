from __future__ import annotations

import pandas as pd


def build_draft_picks_snapshot(ctx: dict) -> pd.DataFrame:
    picks = ctx.get("draft_picks", pd.DataFrame()).copy()

    if picks.empty:
        return pd.DataFrame()

    keep_cols = [
        "id",
        "league_id",
        "season",
        "round",
        "original_team",
        "current_owner",
        "original_pick_rank",
        "pick_label",
        "pick_type",
        "note",
    ]

    existing_cols = [c for c in keep_cols if c in picks.columns]
    df = picks[existing_cols].copy()

    for col in ["season", "round", "original_pick_rank"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["original_team", "current_owner", "pick_label", "pick_type", "note"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    return df.sort_values(
        ["season", "round", "original_pick_rank", "original_team"],
        na_position="last",
    ).reset_index(drop=True)
