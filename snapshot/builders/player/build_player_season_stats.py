from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import nfl_data_py as nfl

from auth import service_client


START_YEAR = 2005
END_YEAR = datetime.now().year


def num(s, default=0):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def pick_col(df, options, default=None):
    for c in options:
        if c in df.columns:
            return c
    return default


def classify_player_style(row):
    pos = row["pos"]
    carries = float(row.get("carries", 0) or 0)
    targets = float(row.get("targets", 0) or 0)
    receptions = float(row.get("receptions", 0) or 0)
    opportunities = float(row.get("opportunities", 0) or 0)
    ypc = float(row.get("yards_per_carry", 0) or 0)
    ypt = float(row.get("yards_per_target", 0) or 0)
    air_yards = float(row.get("receiving_air_yards", 0) or 0)
    pass_attempts = float(row.get("passing_attempts", 0) or 0)
    rush_attempts = carries

    if pos == "QB":
        if rush_attempts >= 70:
            return "DUAL_THREAT_QB"
        if pass_attempts >= 500:
            return "VOLUME_PASSER_QB"
        if pass_attempts >= 300:
            return "SYSTEM_PASSER_QB"
        return "LOW_VOLUME_QB"

    if pos == "RB":
        if opportunities >= 260 and targets >= 50:
            return "THREE_DOWN_RB"
        if carries >= 220:
            return "POWER_VOLUME_RB"
        if targets >= 60:
            return "PASS_CATCHING_RB"
        if opportunities >= 140:
            return "COMMITTEE_RB"
        return "DEPTH_RB"

    if pos == "WR":
        if targets >= 120 and air_yards >= 1200:
            return "ALPHA_VERTICAL_WR"
        if targets >= 120:
            return "ALPHA_VOLUME_WR"
        if targets >= 80 and ypt >= 9:
            return "EFFICIENCY_WR"
        if targets >= 60:
            return "STARTING_WR"
        return "DEPTH_WR"

    if pos == "TE":
        if targets >= 90:
            return "FEATURED_RECEIVING_TE"
        if targets >= 60:
            return "STARTING_RECEIVING_TE"
        if targets >= 30:
            return "ROTATIONAL_TE"
        return "DEPTH_TE"

    return "UNKNOWN"


def load_available_weekly_data(seasons):
    frames = []
    loaded_years = []
    skipped_years = []

    for year in seasons:
        try:
            print(f"Loading weekly data for {year}...")
            one = nfl.import_weekly_data([year], downcast=True)

            if one is not None and not one.empty:
                frames.append(one)
                loaded_years.append(year)
                print(f"  loaded {year}: {len(one)} rows")
            else:
                skipped_years.append(year)
                print(f"  skipped {year}: empty")

        except Exception as e:
            skipped_years.append(year)
            print(f"  skipped {year}: {type(e).__name__}: {e}")

    if not frames:
        raise RuntimeError("No weekly data loaded.")

    print(f"Loaded seasons: {loaded_years}")
    print(f"Skipped seasons: {skipped_years}")

    return pd.concat(frames, ignore_index=True)


def build_player_season_stats(start_year=START_YEAR, end_year=END_YEAR):
    sb = service_client()
    seasons = list(range(start_year, end_year + 1))

    print(f"Loading weekly player data: {start_year}-{end_year}")
    weekly = load_available_weekly_data(seasons)

    name_col = pick_col(weekly, ["player_display_name", "player_name", "name"])
    sleeper_col = pick_col(weekly, ["sleeper_id"])
    gsis_col = pick_col(weekly, ["player_id", "gsis_id"])
    pos_col = pick_col(weekly, ["position", "recent_team_position", "pos"])
    team_col = pick_col(weekly, ["recent_team", "team", "posteam"])

    if not name_col:
        raise RuntimeError("Could not find player name column.")

    df = weekly.copy()

    df["player_name"] = df[name_col]
    df["sleeper_id"] = df[sleeper_col].astype(str) if sleeper_col else None
    df["gsis_id"] = df[gsis_col].astype(str) if gsis_col else None
    df["pos"] = df[pos_col].astype(str) if pos_col else "UNK"
    df["team"] = df[team_col].astype(str) if team_col else None

    if df["sleeper_id"].isna().all():
        df["sleeper_id"] = df["gsis_id"]

    numeric_cols = [
        "fantasy_points",
        "fantasy_points_ppr",
        "passing_yards",
        "passing_tds",
        "interceptions",
        "passing_attempts",
        "attempts",
        "completions",
        "carries",
        "rushing_yards",
        "rushing_tds",
        "targets",
        "receptions",
        "receiving_yards",
        "receiving_tds",
        "receiving_air_yards",
        "receiving_first_downs",
        "rushing_first_downs",
        "target_share",
        "air_yards_share",
        "wopr",
        "racr",
    ]

    for c in numeric_cols:
        if c not in df.columns:
            df[c] = 0
        df[c] = num(df[c])

    if "passing_attempts" not in weekly.columns and "attempts" in weekly.columns:
        df["passing_attempts"] = df["attempts"]

    df["touches"] = df["carries"] + df["receptions"]
    df["opportunities"] = df["carries"] + df["targets"]

    df["yards_per_touch_week"] = np.where(
        df["touches"] > 0,
        (df["rushing_yards"] + df["receiving_yards"]) / df["touches"],
        0,
    )

    df["yards_per_target_week"] = np.where(
        df["targets"] > 0,
        df["receiving_yards"] / df["targets"],
        0,
    )

    df["yards_per_carry_week"] = np.where(
        df["carries"] > 0,
        df["rushing_yards"] / df["carries"],
        0,
    )

    df = df[df["pos"].isin(["QB", "RB", "WR", "TE"])].copy()

    season_rows = (
        df.groupby(["season", "sleeper_id", "gsis_id", "player_name", "pos"], dropna=False)
        .agg(
            team=("team", "last"),
            games=("week", "nunique"),
            fantasy_points=("fantasy_points", "sum"),
            fantasy_points_ppr=("fantasy_points_ppr", "sum"),
            passing_yards=("passing_yards", "sum"),
            passing_tds=("passing_tds", "sum"),
            interceptions=("interceptions", "sum"),
            passing_attempts=("passing_attempts", "sum"),
            completions=("completions", "sum"),
            carries=("carries", "sum"),
            rushing_yards=("rushing_yards", "sum"),
            rushing_tds=("rushing_tds", "sum"),
            targets=("targets", "sum"),
            receptions=("receptions", "sum"),
            receiving_yards=("receiving_yards", "sum"),
            receiving_tds=("receiving_tds", "sum"),
            receiving_air_yards=("receiving_air_yards", "sum"),
            receiving_first_downs=("receiving_first_downs", "sum"),
            rushing_first_downs=("rushing_first_downs", "sum"),
            touches=("touches", "sum"),
            opportunities=("opportunities", "sum"),
            target_share=("target_share", "mean"),
            air_yards_share=("air_yards_share", "mean"),
            wopr=("wopr", "mean"),
            racr=("racr", "mean"),
        )
        .reset_index()
    )

    season_rows["fantasy_ppg"] = np.where(
        season_rows["games"] > 0,
        season_rows["fantasy_points"] / season_rows["games"],
        0,
    )

    season_rows["fantasy_ppg_ppr"] = np.where(
        season_rows["games"] > 0,
        season_rows["fantasy_points_ppr"] / season_rows["games"],
        0,
    )

    season_rows["yards_per_touch"] = np.where(
        season_rows["touches"] > 0,
        (season_rows["rushing_yards"] + season_rows["receiving_yards"])
        / season_rows["touches"],
        0,
    )

    season_rows["yards_per_target"] = np.where(
        season_rows["targets"] > 0,
        season_rows["receiving_yards"] / season_rows["targets"],
        0,
    )

    season_rows["yards_per_carry"] = np.where(
        season_rows["carries"] > 0,
        season_rows["rushing_yards"] / season_rows["carries"],
        0,
    )

    season_rows["catch_rate"] = np.where(
        season_rows["targets"] > 0,
        season_rows["receptions"] / season_rows["targets"],
        0,
    )

    season_rows["completion_rate"] = np.where(
        season_rows["passing_attempts"] > 0,
        season_rows["completions"] / season_rows["passing_attempts"],
        0,
    )

    season_rows["receiving_first_down_rate"] = np.where(
        season_rows["targets"] > 0,
        season_rows["receiving_first_downs"] / season_rows["targets"],
        0,
    )

    season_rows["rushing_first_down_rate"] = np.where(
        season_rows["carries"] > 0,
        season_rows["rushing_first_downs"] / season_rows["carries"],
        0,
    )

    season_rows["player_style"] = season_rows.apply(classify_player_style, axis=1)

    season_rows["position_rank"] = (
        season_rows.groupby(["season", "pos"])["fantasy_points_ppr"]
        .rank(method="first", ascending=False)
        .astype(int)
    )

    season_rows["overall_rank"] = (
        season_rows.groupby(["season"])["fantasy_points_ppr"]
        .rank(method="first", ascending=False)
        .astype(int)
    )

    season_rows["source"] = "nfl_data_py_weekly"
    season_rows["updated_at"] = datetime.now(timezone.utc).isoformat()

    numeric_round_cols = [
        "fantasy_points",
        "fantasy_points_ppr",
        "fantasy_ppg",
        "fantasy_ppg_ppr",
        "passing_yards",
        "passing_tds",
        "interceptions",
        "passing_attempts",
        "completions",
        "carries",
        "rushing_yards",
        "rushing_tds",
        "targets",
        "receptions",
        "receiving_yards",
        "receiving_tds",
        "receiving_air_yards",
        "receiving_first_downs",
        "rushing_first_downs",
        "touches",
        "opportunities",
        "target_share",
        "air_yards_share",
        "wopr",
        "racr",
        "yards_per_touch",
        "yards_per_target",
        "yards_per_carry",
        "catch_rate",
        "completion_rate",
        "receiving_first_down_rate",
        "rushing_first_down_rate",
    ]

    for c in numeric_round_cols:
        if c in season_rows.columns:
            season_rows[c] = num(season_rows[c]).round(3)

    season_rows = season_rows.replace({np.nan: None})

    rows = season_rows.to_dict("records")

    print(f"Prepared season rows: {len(rows)}")

    if rows:
        sb.table("player_season_stats").upsert(
            rows,
            on_conflict="season,sleeper_id",
        ).execute()

    print(f"Upserted player season stat rows: {len(rows)}")
    print(season_rows.head(40).to_string(index=False))

    return season_rows


if __name__ == "__main__":
    build_player_season_stats()
