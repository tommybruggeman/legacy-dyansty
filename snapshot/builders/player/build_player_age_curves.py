from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import nfl_data_py as nfl

from auth import service_client


START_YEAR = 2005
END_YEAR = datetime.now().year

RECENCY_BUCKET_YEARS = 5
RECENCY_BUCKET_DECAY = 0.50
MIN_SEASON_WEIGHT = 0.10
MIN_WEIGHTED_SAMPLE = 7.0


def num(s, default=0):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def pick_col(df, options, default=None):
    for c in options:
        if c in df.columns:
            return c
    return default


def season_weight(season, latest_season):
    years_old = int(latest_season) - int(season)
    bucket = max(years_old // RECENCY_BUCKET_YEARS, 0)
    return max(RECENCY_BUCKET_DECAY ** bucket, MIN_SEASON_WEIGHT)


def weighted_avg(values, weights):
    values = pd.to_numeric(values, errors="coerce").fillna(0)
    weights = pd.to_numeric(weights, errors="coerce").fillna(0)
    total_weight = weights.sum()

    if total_weight <= 0:
        return 0

    return float((values * weights).sum() / total_weight)


def weighted_q(values, weights, q):
    values = pd.to_numeric(values, errors="coerce").fillna(0).to_numpy()
    weights = pd.to_numeric(weights, errors="coerce").fillna(0).to_numpy()

    if len(values) == 0 or weights.sum() <= 0:
        return 0

    sorter = np.argsort(values)
    values = values[sorter]
    weights = weights[sorter]

    cumulative = np.cumsum(weights)
    cutoff = q * weights.sum()

    return float(values[np.searchsorted(cumulative, cutoff)])


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


def load_players():
    print("Loading player metadata...")
    players = nfl.import_players()

    id_col = pick_col(players, ["gsis_id", "player_id", "pfr_id", "sleeper_id"])
    birth_col = pick_col(players, ["birth_date", "birthdate", "dob"])
    name_col = pick_col(players, ["display_name", "player_name", "full_name", "name"])
    pos_col = pick_col(players, ["position", "position_group"])

    if not id_col:
        raise RuntimeError("Could not find player id column in player metadata.")

    if not birth_col:
        raise RuntimeError("Could not find birth date column in player metadata.")

    out = players.copy()
    out["player_id"] = out[id_col]
    out["birth_date"] = pd.to_datetime(out[birth_col], errors="coerce")

    if name_col:
        out["metadata_player_name"] = out[name_col]

    if pos_col:
        out["metadata_pos"] = out[pos_col]

    out = out[["player_id", "birth_date"]].dropna(subset=["player_id", "birth_date"])
    out = out.drop_duplicates(subset=["player_id"])

    print(f"Loaded player birth dates: {len(out)}")

    return out


def build_player_age_curves(start_year=START_YEAR, end_year=END_YEAR):
    sb = service_client()
    seasons = list(range(start_year, end_year + 1))

    print(f"Loading weekly data for true age curves: {start_year}-{end_year}")
    weekly = load_available_weekly_data(seasons)
    players = load_players()

    name_col = pick_col(weekly, ["player_display_name", "player_name", "name"])
    id_col = pick_col(weekly, ["player_id", "gsis_id", "pfr_id"])
    pos_col = pick_col(weekly, ["position", "recent_team_position"])

    if not name_col:
        raise RuntimeError("Could not find player name column.")

    df = weekly.copy()

    df["player_name"] = df[name_col]
    df["player_id"] = df[id_col] if id_col else df["player_name"]
    df["pos"] = df[pos_col] if pos_col else "UNK"

    for c in [
        "fantasy_points_ppr",
        "carries",
        "targets",
        "receptions",
        "rushing_yards",
        "receiving_yards",
    ]:
        if c not in df.columns:
            df[c] = 0
        df[c] = num(df[c])

    df["opportunities"] = df["carries"] + df["targets"]
    df["touches"] = df["carries"] + df["receptions"]
    df["total_yards"] = df["rushing_yards"] + df["receiving_yards"]

    season_rows = (
        df.groupby(["season", "player_id", "player_name", "pos"], dropna=False)
        .agg(
            games=("week", "nunique"),
            fantasy_points_ppr=("fantasy_points_ppr", "sum"),
            carries=("carries", "sum"),
            targets=("targets", "sum"),
            receptions=("receptions", "sum"),
            opportunities=("opportunities", "sum"),
            touches=("touches", "sum"),
            total_yards=("total_yards", "sum"),
        )
        .reset_index()
    )

    season_rows["pos"] = season_rows["pos"].fillna("UNK").astype(str)
    season_rows = season_rows[season_rows["pos"].isin(["QB", "RB", "WR", "TE"])].copy()

    season_rows = season_rows.merge(
        players,
        on="player_id",
        how="left",
    )

    before_age_filter = len(season_rows)

    season_rows["age"] = (
        season_rows["season"] - season_rows["birth_date"].dt.year
    )

    season_rows = season_rows.dropna(subset=["age"]).copy()
    season_rows["age"] = season_rows["age"].astype(int)

    season_rows = season_rows[
        season_rows["age"].between(20, 45)
    ].copy()

    print(f"Rows before age filter: {before_age_filter}")
    print(f"Rows after age filter: {len(season_rows)}")

    season_rows["fantasy_ppg_ppr"] = np.where(
        season_rows["games"] > 0,
        season_rows["fantasy_points_ppr"] / season_rows["games"],
        0,
    )

    season_rows["yards_per_touch"] = np.where(
        season_rows["touches"] > 0,
        season_rows["total_yards"] / season_rows["touches"],
        0,
    )

    season_rows["elite_15_ppg"] = np.where(
        season_rows["fantasy_ppg_ppr"] >= 15,
        1,
        0,
    )

    latest_loaded_season = int(season_rows["season"].max())

    season_rows["season_weight"] = season_rows["season"].apply(
        lambda s: season_weight(s, latest_loaded_season)
    )

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for (pos, age), g in season_rows.groupby(["pos", "age"]):
        w = g["season_weight"]

        rows.append(
            {
                "pos": pos,
                "age": int(age),
                "sample_size": int(len(g)),
                "weighted_sample_size": round(float(w.sum()), 3),
                "latest_loaded_season": latest_loaded_season,
                "avg_fantasy_ppg_ppr": weighted_avg(g["fantasy_ppg_ppr"], w),
                "median_fantasy_ppg_ppr": weighted_q(g["fantasy_ppg_ppr"], w, 0.50),
                "avg_fantasy_points_ppr": weighted_avg(g["fantasy_points_ppr"], w),
                "avg_games": weighted_avg(g["games"], w),
                "avg_opportunities": weighted_avg(g["opportunities"], w),
                "avg_touches": weighted_avg(g["touches"], w),
                "avg_targets": weighted_avg(g["targets"], w),
                "avg_carries": weighted_avg(g["carries"], w),
                "avg_yards_per_touch": weighted_avg(g["yards_per_touch"], w),
                "p75_fantasy_ppg_ppr": weighted_q(g["fantasy_ppg_ppr"], w, 0.75),
                "p90_fantasy_ppg_ppr": weighted_q(g["fantasy_ppg_ppr"], w, 0.90),
                "elite_rate_15_ppg": weighted_avg(g["elite_15_ppg"], w),
                "updated_at": now,
            }
        )

    grouped = pd.DataFrame(rows)

    grouped = grouped[
        grouped["weighted_sample_size"] >= MIN_WEIGHTED_SAMPLE
    ].copy()

    numeric_cols = [
        "weighted_sample_size",
        "avg_fantasy_ppg_ppr",
        "median_fantasy_ppg_ppr",
        "avg_fantasy_points_ppr",
        "avg_games",
        "avg_opportunities",
        "avg_touches",
        "avg_targets",
        "avg_carries",
        "avg_yards_per_touch",
        "p75_fantasy_ppg_ppr",
        "p90_fantasy_ppg_ppr",
        "elite_rate_15_ppg",
    ]

    for c in numeric_cols:
        grouped[c] = grouped[c].round(3)

    grouped = grouped.sort_values(["pos", "age"])

    for pos in grouped["pos"].unique():
        mask = grouped["pos"] == pos
        grouped.loc[mask, "delta_avg_fantasy_ppg_ppr"] = (
            grouped.loc[mask, "avg_fantasy_ppg_ppr"].diff()
        )
        grouped.loc[mask, "delta_avg_opportunities"] = (
            grouped.loc[mask, "avg_opportunities"].diff()
        )
        grouped.loc[mask, "delta_avg_yards_per_touch"] = (
            grouped.loc[mask, "avg_yards_per_touch"].diff()
        )
        grouped.loc[mask, "delta_elite_rate_15_ppg"] = (
            grouped.loc[mask, "elite_rate_15_ppg"].diff()
        )

    delta_cols = [
        "delta_avg_fantasy_ppg_ppr",
        "delta_avg_opportunities",
        "delta_avg_yards_per_touch",
        "delta_elite_rate_15_ppg",
    ]

    for c in delta_cols:
        grouped[c] = grouped[c].fillna(0).round(3)

    out = grouped.to_dict("records")

    sb.table("historical_player_age_curve_features").upsert(
        out,
        on_conflict="pos,age",
    ).execute()

    print(f"Upserted true age curve rows: {len(out)}")
    print(grouped.head(120).to_string(index=False))

    return grouped


if __name__ == "__main__":
    build_player_age_curves()
