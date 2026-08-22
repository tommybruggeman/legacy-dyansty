from __future__ import annotations

import pandas as pd


def _clean_id(df: pd.DataFrame) -> pd.DataFrame:
    if not df.empty and "sleeper_id" in df.columns:
        df["sleeper_id"] = df["sleeper_id"].astype(str).str.strip()
    return df


def build_players_snapshot(ctx: dict) -> pd.DataFrame:
    rankings = _clean_id(ctx.get("player_rankings_snapshot", pd.DataFrame()).copy())
    career = _clean_id(ctx.get("player_career_features_snapshot", pd.DataFrame()).copy())
    engine = _clean_id(ctx.get("player_engine_scores", pd.DataFrame()).copy())

    if engine.empty:
        return pd.DataFrame()

    df = engine.copy()

    if not rankings.empty:
        ranking_cols = [
            "sleeper_id",
            "dynasty_rank",
            "position_rank",
            "tier",
            "base_player_score",
            "source",
            "updated_at",
        ]
        ranking_cols = [c for c in ranking_cols if c in rankings.columns]

        df = df.merge(
            rankings[ranking_cols],
            on="sleeper_id",
            how="left",
            suffixes=("", "_ranking"),
        )

    if not career.empty:
        career_cols = [
            "sleeper_id",
            "first_season",
            "latest_season",
            "seasons_played",
            "career_games",
            "career_fantasy_points_ppr",
            "career_ppg_ppr",
            "best_season",
            "best_season_points_ppr",
            "best_season_ppg_ppr",
            "last_1_season_ppg_ppr",
            "last_2_seasons_ppg_ppr",
            "last_3_seasons_ppg_ppr",
            "production_trend_score",
            "experience_score",
        ]
        career_cols = [c for c in career_cols if c in career.columns]

        df = df.merge(
            career[career_cols],
            on="sleeper_id",
            how="left",
        )

    if "base_player_score_ranking" in df.columns:
        df["dynasty_base_player_score"] = df["base_player_score_ranking"]
        df = df.drop(columns=["base_player_score_ranking"])

    return df.sort_values("engine_score", ascending=False).reset_index(drop=True)
