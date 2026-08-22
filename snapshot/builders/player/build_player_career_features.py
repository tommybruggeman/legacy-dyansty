from __future__ import annotations

# ============================================================
# Imports
# ============================================================

import os
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv


# ============================================================
# Supabase Client
# ============================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================
# Fetch Helpers
# ============================================================

def fetch_player_season_stats() -> pd.DataFrame:
    res = (
        sb.table("player_season_stats")
        .select("*")
        .execute()
    )
    return pd.DataFrame(res.data or [])


# ============================================================
# Feature Builder
# ============================================================

def build_player_career_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["games"] = pd.to_numeric(df["games"], errors="coerce").fillna(0)
    df["fantasy_points_ppr"] = pd.to_numeric(
        df["fantasy_points_ppr"],
        errors="coerce"
    ).fillna(0)
    df["fantasy_ppg_ppr"] = pd.to_numeric(
        df["fantasy_ppg_ppr"],
        errors="coerce"
    ).fillna(0)
    df["position_rank"] = pd.to_numeric(df["position_rank"], errors="coerce")
    df["overall_rank"] = pd.to_numeric(df["overall_rank"], errors="coerce")

    df = df.dropna(subset=["sleeper_id", "season"])

    rows = []

    for sleeper_id, g in df.groupby("sleeper_id"):
        g = g.sort_values("season")

        latest = g.iloc[-1]
        best = g.sort_values("fantasy_points_ppr", ascending=False).iloc[0]

        last_1 = g.tail(1)
        last_2 = g.tail(2)
        last_3 = g.tail(3)

        career_games = float(g["games"].sum())
        career_points = float(g["fantasy_points_ppr"].sum())

        row = {
            "sleeper_id": str(sleeper_id),
            "player_name": latest["player_name"],
            "pos": latest["pos"],

            "first_season": int(g["season"].min()),
            "latest_season": int(g["season"].max()),
            "seasons_played": int(g["season"].nunique()),
            "career_games": int(career_games),

            "career_fantasy_points_ppr": career_points,
            "career_ppg_ppr": career_points / max(career_games, 1),

            "best_season": int(best["season"]),
            "best_season_points_ppr": float(best["fantasy_points_ppr"]),
            "best_season_ppg_ppr": float(best["fantasy_ppg_ppr"]),

            "last_1_season_points_ppr": float(last_1["fantasy_points_ppr"].sum()),
            "last_2_seasons_points_ppr": float(last_2["fantasy_points_ppr"].sum()),
            "last_3_seasons_points_ppr": float(last_3["fantasy_points_ppr"].sum()),

            "last_1_season_ppg_ppr": float(last_1["fantasy_ppg_ppr"].mean()),
            "last_2_seasons_ppg_ppr": float(last_2["fantasy_ppg_ppr"].mean()),
            "last_3_seasons_ppg_ppr": float(last_3["fantasy_ppg_ppr"].mean()),

            "career_position_rank_avg": float(g["position_rank"].mean()),
            "best_position_rank": int(g["position_rank"].min()),
            "latest_position_rank": int(latest["position_rank"]),

            "production_trend_score": float(
                last_1["fantasy_ppg_ppr"].mean()
                - last_3["fantasy_ppg_ppr"].mean()
            ),
            "durability_score": float(g.tail(3)["games"].mean()),
            "experience_score": float(g["season"].nunique()),
            "age_curve_score": None,
        }

        rows.append(row)

    return pd.DataFrame(rows)


# ============================================================
# Main
# ============================================================

def main():
    season_stats = fetch_player_season_stats()
    print(f"Fetched player season rows: {len(season_stats)}")

    features = build_player_career_features(season_stats)
    print(f"Built career feature rows: {len(features)}")

    if features.empty:
        print("No career features built. Skipping upsert.")
        return

    records = features.where(pd.notnull(features), None).to_dict("records")

    sb.table("player_career_features").upsert(
        records,
        on_conflict="sleeper_id"
    ).execute()

    print("Upserted player_career_features.")


if __name__ == "__main__":
    main()