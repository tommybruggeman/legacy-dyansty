from __future__ import annotations

# ============================================================
# Scoring History Import
#
# Imports historical weekly NFL player stats from nflverse
# and writes season-level fantasy production into Supabase.
# ============================================================

from io import StringIO

import pandas as pd
import requests

from auth import service_client


# ============================================================
# Config
# ============================================================

SEASONS = [
    2024,
]

FANTASY_SCORING = {
    "pass_yd": 0.04,
    "pass_td": 4,
    "interception": -2,
    "rush_yd": 0.1,
    "rush_td": 6,
    "rec": 0.5,
    "rec_yd": 0.1,
    "rec_td": 6,
    "fumble_lost": -2,
}


# ============================================================
# Supabase Client
# ============================================================

supabase = service_client()


# ============================================================
# URLs
# ============================================================

def player_stats_url(season: int) -> str:
    return (
        "https://github.com/nflverse/nflverse-data/releases/download/"
        f"player_stats/stats_player_week_{season}.csv"
    )


# ============================================================
# Load Weekly Stats
# ============================================================

def load_weekly_stats(season: int) -> pd.DataFrame:
    url = player_stats_url(season)

    res = requests.get(url, timeout=60)
    res.raise_for_status()

    print(f"Loaded weekly stats: {season}")

    return pd.read_csv(StringIO(res.text))


# ============================================================
# Helpers
# ============================================================

def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return 0

    return pd.to_numeric(
        df[col],
        errors="coerce",
    ).fillna(0)


# ============================================================
# Calculate Fantasy Points
# ============================================================

def add_fantasy_points(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["fantasy_points_custom"] = (
        _num(out, "passing_yards") * FANTASY_SCORING["pass_yd"]
        + _num(out, "passing_tds") * FANTASY_SCORING["pass_td"]
        + _num(out, "interceptions") * FANTASY_SCORING["interception"]
        + _num(out, "rushing_yards") * FANTASY_SCORING["rush_yd"]
        + _num(out, "rushing_tds") * FANTASY_SCORING["rush_td"]
        + _num(out, "receptions") * FANTASY_SCORING["rec"]
        + _num(out, "receiving_yards") * FANTASY_SCORING["rec_yd"]
        + _num(out, "receiving_tds") * FANTASY_SCORING["rec_td"]
        + _num(out, "sack_fumbles_lost") * FANTASY_SCORING["fumble_lost"]
        + _num(out, "rushing_fumbles_lost") * FANTASY_SCORING["fumble_lost"]
        + _num(out, "receiving_fumbles_lost") * FANTASY_SCORING["fumble_lost"]
    )

    return out


# ============================================================
# Build Season Summary
# ============================================================

def build_season_summary(df: pd.DataFrame, season: int) -> pd.DataFrame:
    if df.empty:
        return df

    df = add_fantasy_points(df)

    id_col = "player_id" if "player_id" in df.columns else "recent_team"
    name_col = "player_name" if "player_name" in df.columns else "player_display_name"

    summary = (
        df.groupby(
            [
                id_col,
                name_col,
                "position",
            ],
            dropna=False,
        )
        .agg(
            games_played=("week", "nunique"),
            total_points=("fantasy_points_custom", "sum"),
        )
        .reset_index()
    )

    summary["season"] = season

    summary["ppg"] = (
        summary["total_points"]
        / summary["games_played"].replace(0, 1)
    ).round(2)

    summary["positional_ppg_rank"] = (
        summary.groupby("position")["ppg"]
        .rank(ascending=False, method="min")
    )

    summary["positional_total_rank"] = (
        summary.groupby("position")["total_points"]
        .rank(ascending=False, method="min")
    )

    summary = summary.rename(
        columns={
            id_col: "sleeper_id",
            name_col: "player",
            "position": "pos",
        }
    )

    final = summary[
        [
            "sleeper_id",
            "player",
            "pos",
            "season",
            "games_played",
            "total_points",
            "ppg",
            "positional_ppg_rank",
            "positional_total_rank",
        ]
    ].copy()

    final["sleeper_id"] = final["sleeper_id"].astype(str)
    final["source"] = "nflverse_player_stats"

    return final


# ============================================================
# Upsert
# ============================================================

def upsert_scoring_history(df: pd.DataFrame):
    if df.empty:
        print("No scoring history to upsert.")
        return

    clean = df.copy()

    clean = clean.where(
        pd.notnull(clean),
        None,
    )

    clean = clean.drop_duplicates(
        subset=[
            "sleeper_id",
            "season",
        ],
        keep="first",
    )

    rows = clean.to_dict(orient="records")

    supabase.table("player_scoring_history").upsert(
        rows,
        on_conflict="sleeper_id,season",
    ).execute()

    print(f"Upserted {len(rows)} scoring history rows.")


# ============================================================
# Run Import
# ============================================================

def run_scoring_history_import():
    all_rows = []

    for season in SEASONS:
        weekly = load_weekly_stats(season)
        summary = build_season_summary(weekly, season)
        all_rows.append(summary)

    final = pd.concat(
        all_rows,
        ignore_index=True,
    )

    print(final.head())
    print(f"Rows ready: {len(final)}")

    upsert_scoring_history(final)

    return final


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    run_scoring_history_import()