from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import nfl_data_py as nfl

from auth import service_client


START_YEAR = 2005
END_YEAR = datetime.now().year


def safe_num(s, default=0):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def pick_col(df, options, default=None):
    for c in options:
        if c in df.columns:
            return c
    return default

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
        raise RuntimeError("No weekly data loaded for any requested season.")

    print(f"Loaded seasons: {loaded_years}")
    print(f"Skipped seasons: {skipped_years}")

    return pd.concat(frames, ignore_index=True)


def z_score(series):
    s = pd.to_numeric(series, errors="coerce")
    std = s.std()
    if not std or pd.isna(std):
        return pd.Series([0] * len(s), index=s.index)
    return (s - s.mean()) / std


def build_historical_player_features(start_year=START_YEAR, end_year=END_YEAR):
    seasons = list(range(start_year, end_year + 1))

    print(f"Loading nflverse seasonal data: {start_year}-{end_year}")
    weekly = load_available_weekly_data(seasons)

    if weekly.empty:
        raise RuntimeError("No weekly data loaded.")

    print(f"Loaded weekly rows: {len(weekly)}")

    name_col = pick_col(weekly, ["player_display_name", "player_name", "name"])
    id_col = pick_col(weekly, ["player_id", "gsis_id", "pfr_id"])
    pos_col = pick_col(weekly, ["position", "recent_team_position"])
    team_col = pick_col(weekly, ["recent_team", "team"])

    if not name_col:
        raise RuntimeError("Could not find player name column.")

    df = weekly.copy()

    df["player_name"] = df[name_col]
    df["player_id"] = df[id_col] if id_col else None
    df["pos"] = df[pos_col] if pos_col else None
    df["team"] = df[team_col] if team_col else None

    # Core stats
    for c in [
        "passing_yards", "passing_tds", "interceptions",
        "attempts", "completions",
        "carries", "rushing_yards", "rushing_tds",
        "targets", "receptions", "receiving_yards", "receiving_tds",
        "receiving_air_yards", "air_yards",
        "wopr", "racr", "target_share", "air_yards_share",
        "fantasy_points", "fantasy_points_ppr",
    ]:
        if c not in df.columns:
            df[c] = 0

    df["passing_yards"] = safe_num(df["passing_yards"])
    df["passing_tds"] = safe_num(df["passing_tds"])
    df["interceptions"] = safe_num(df["interceptions"])
    df["attempts"] = safe_num(df["attempts"])
    df["completions"] = safe_num(df["completions"])

    df["carries"] = safe_num(df["carries"])
    df["rushing_yards"] = safe_num(df["rushing_yards"])
    df["rushing_tds"] = safe_num(df["rushing_tds"])

    df["targets"] = safe_num(df["targets"])
    df["receptions"] = safe_num(df["receptions"])
    df["receiving_yards"] = safe_num(df["receiving_yards"])
    df["receiving_tds"] = safe_num(df["receiving_tds"])

    df["fantasy_points"] = safe_num(df["fantasy_points"])
    df["fantasy_points_ppr"] = safe_num(df["fantasy_points_ppr"])

    df["opportunities"] = df["carries"] + df["targets"]
    df["touches"] = df["carries"] + df["receptions"]
    df["total_yards"] = df["rushing_yards"] + df["receiving_yards"]
    df["total_tds"] = df["rushing_tds"] + df["receiving_tds"]
    df["qb_volume"] = df["attempts"] + df["carries"]

    # Season-level aggregation
    grouped = (
        df.groupby(["season", "player_id", "player_name", "pos"], dropna=False)
        .agg(
            team=("team", "last"),
            games=("week", "nunique"),

            passing_yards=("passing_yards", "sum"),
            passing_tds=("passing_tds", "sum"),
            interceptions=("interceptions", "sum"),
            pass_attempts=("attempts", "sum"),
            completions=("completions", "sum"),

            carries=("carries", "sum"),
            rushing_yards=("rushing_yards", "sum"),
            rushing_tds=("rushing_tds", "sum"),

            targets=("targets", "sum"),
            receptions=("receptions", "sum"),
            receiving_yards=("receiving_yards", "sum"),
            receiving_tds=("receiving_tds", "sum"),

            opportunities=("opportunities", "sum"),
            touches=("touches", "sum"),
            total_yards=("total_yards", "sum"),
            total_tds=("total_tds", "sum"),
            qb_volume=("qb_volume", "sum"),

            fantasy_points=("fantasy_points", "sum"),
            fantasy_points_ppr=("fantasy_points_ppr", "sum"),
        )
        .reset_index()
    )

    grouped["fantasy_ppg_ppr"] = np.where(
        grouped["games"] > 0,
        grouped["fantasy_points_ppr"] / grouped["games"],
        0,
    )

    grouped["yards_per_touch"] = np.where(
        grouped["touches"] > 0,
        grouped["total_yards"] / grouped["touches"],
        0,
    )

    grouped["yards_per_target"] = np.where(
        grouped["targets"] > 0,
        grouped["receiving_yards"] / grouped["targets"],
        0,
    )

    grouped["yards_per_carry"] = np.where(
        grouped["carries"] > 0,
        grouped["rushing_yards"] / grouped["carries"],
        0,
    )

    grouped["pos"] = grouped["pos"].fillna("UNK").astype(str)

    grouped["position_rank_ppr"] = (
        grouped.groupby(["season", "pos"])["fantasy_points_ppr"]
        .rank(method="first", ascending=False)
        .fillna(9999)
        .astype(int)
    )

    grouped["overall_rank_ppr"] = (
        grouped.groupby(["season"])["fantasy_points_ppr"]
        .rank(method="first", ascending=False)
        .fillna(9999)
        .astype(int)
    )

    # Career-level features
    now = datetime.now(timezone.utc).isoformat()

    career_rows = []

    for player_id, p in grouped.groupby("player_id", dropna=False):
        p = p.sort_values("season").copy()

        latest = p.iloc[-1]
        recent3 = p[p["season"] >= end_year - 2].copy()
        recent5 = p[p["season"] >= end_year - 4].copy()

        weights = {end_year: 1.00, end_year - 1: 0.70, end_year - 2: 0.50}
        recent3["weight"] = recent3["season"].map(weights).fillna(0.30)

        weighted_fp = (
            (recent3["fantasy_points_ppr"] * recent3["weight"]).sum()
            / recent3["weight"].sum()
            if not recent3.empty and recent3["weight"].sum() > 0
            else 0
        )

        weighted_ppg = (
            (recent3["fantasy_ppg_ppr"] * recent3["weight"]).sum()
            / recent3["weight"].sum()
            if not recent3.empty and recent3["weight"].sum() > 0
            else 0
        )

        first_season = int(p["season"].min())
        last_season = int(p["season"].max())
        seasons_played = int(p["season"].nunique())

        peak = p.sort_values("fantasy_points_ppr", ascending=False).iloc[0]

        career_rows.append({
            "player_id": player_id,
            "player_name": latest["player_name"],
            "pos": latest["pos"],
            "latest_team": latest["team"],

            "first_season": first_season,
            "last_season": last_season,
            "seasons_played": seasons_played,

            "career_games": int(p["games"].sum()),
            "career_fantasy_points_ppr": round(float(p["fantasy_points_ppr"].sum()), 2),
            "career_touches": round(float(p["touches"].sum()), 2),
            "career_opportunities": round(float(p["opportunities"].sum()), 2),
            "career_carries": round(float(p["carries"].sum()), 2),
            "career_targets": round(float(p["targets"].sum()), 2),
            "career_receptions": round(float(p["receptions"].sum()), 2),
            "career_total_yards": round(float(p["total_yards"].sum()), 2),
            "career_total_tds": round(float(p["total_tds"].sum()), 2),

            "last_season_games": int(latest["games"]),
            "last_season_fantasy_points_ppr": round(float(latest["fantasy_points_ppr"]), 2),
            "last_season_fantasy_ppg_ppr": round(float(latest["fantasy_ppg_ppr"]), 2),
            "last_season_touches": round(float(latest["touches"]), 2),
            "last_season_opportunities": round(float(latest["opportunities"]), 2),

            "recent3_weighted_fantasy_points_ppr": round(float(weighted_fp), 2),
            "recent3_weighted_fantasy_ppg_ppr": round(float(weighted_ppg), 2),
            "recent3_games": int(recent3["games"].sum()) if not recent3.empty else 0,
            "recent3_touches": round(float(recent3["touches"].sum()), 2) if not recent3.empty else 0,
            "recent3_opportunities": round(float(recent3["opportunities"].sum()), 2) if not recent3.empty else 0,
            "recent3_targets": round(float(recent3["targets"].sum()), 2) if not recent3.empty else 0,
            "recent3_carries": round(float(recent3["carries"].sum()), 2) if not recent3.empty else 0,

            "recent5_games": int(recent5["games"].sum()) if not recent5.empty else 0,
            "recent5_touches": round(float(recent5["touches"].sum()), 2) if not recent5.empty else 0,
            "recent5_opportunities": round(float(recent5["opportunities"].sum()), 2) if not recent5.empty else 0,

            "peak_season": int(peak["season"]),
            "peak_fantasy_points_ppr": round(float(peak["fantasy_points_ppr"]), 2),
            "peak_fantasy_ppg_ppr": round(float(peak["fantasy_ppg_ppr"]), 2),
            "peak_position_rank_ppr": int(peak["position_rank_ppr"]),

            "avg_yards_per_touch": round(float(p["yards_per_touch"].mean()), 3),
            "avg_yards_per_target": round(float(p["yards_per_target"].mean()), 3),
            "avg_yards_per_carry": round(float(p["yards_per_carry"].mean()), 3),

            "updated_at": now,
        })

    career = pd.DataFrame(career_rows)

    # Derived profile scores
    career["production_score"] = (
        z_score(career["recent3_weighted_fantasy_points_ppr"]) * 15 + 50
    ).clip(0, 100).round(2)

    career["weekly_ceiling_score"] = (
        z_score(career["peak_fantasy_ppg_ppr"]) * 15 + 50
    ).clip(0, 100).round(2)

    career["workload_score"] = (
        z_score(career["recent3_opportunities"]) * 15 + 50
    ).clip(0, 100).round(2)

    career["durability_score"] = (
        z_score(career["recent3_games"]) * 15 + 50
    ).clip(0, 100).round(2)

    career["efficiency_score"] = (
        z_score(career["avg_yards_per_touch"]) * 10 + 50
    ).clip(0, 100).round(2)

    rows = career.to_dict("records")

    sb = service_client()

    print(f"Upserting historical player feature rows: {len(rows)}")

    sb.table("historical_player_features").upsert(
        rows,
        on_conflict="player_id",
    ).execute()

    print("Historical player features complete.")

    return career


if __name__ == "__main__":
    df = build_historical_player_features()
    print(df.head(20).to_string(index=False))
