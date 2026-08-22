from __future__ import annotations

import pandas as pd

from auth import service_client


SOURCE_TABLE = "player_season_stats"
TARGET_TABLE = "player_career_features"


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def build_player_career_features() -> pd.DataFrame:
    sb = service_client()

    rows = (
        sb.table(SOURCE_TABLE)
        .select("*")
        .execute()
        .data
        or []
    )

    if not rows:
        print("No player season stats found.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    required = {
        "sleeper_id",
        "player_name",
        "pos",
        "season",
        "games",
        "fantasy_points_ppr",
        "fantasy_ppg_ppr",
        "position_rank",
    }

    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns from {SOURCE_TABLE}: {sorted(missing)}")

    df["season"] = _num(df["season"]).astype(int)
    df["games"] = _num(df["games"])
    df["fantasy_points_ppr"] = _num(df["fantasy_points_ppr"])
    df["fantasy_ppg_ppr"] = _num(df["fantasy_ppg_ppr"])
    df["position_rank"] = _num(df["position_rank"], default=999)

    latest_season = int(df["season"].max())

    grouped = (
        df.sort_values(["sleeper_id", "season"])
        .groupby("sleeper_id", as_index=False)
        .agg(
            player_name=("player_name", "last"),
            pos=("pos", "last"),
            first_season=("season", "min"),
            latest_season=("season", "max"),
            seasons_played=("season", "nunique"),
            career_games=("games", "sum"),
            career_fantasy_points_ppr=("fantasy_points_ppr", "sum"),
            avg_fantasy_ppg_ppr=("fantasy_ppg_ppr", "mean"),
            best_fantasy_ppg_ppr=("fantasy_ppg_ppr", "max"),
            avg_position_rank=("position_rank", "mean"),
            best_position_rank=("position_rank", "min"),
        )
    )

    recent = df[df["season"] >= latest_season - 2].copy()

    recent_grouped = (
        recent.groupby("sleeper_id", as_index=False)
        .agg(
            recent_games=("games", "sum"),
            recent_fantasy_points_ppr=("fantasy_points_ppr", "sum"),
            recent_avg_ppg_ppr=("fantasy_ppg_ppr", "mean"),
            recent_best_ppg_ppr=("fantasy_ppg_ppr", "max"),
            recent_avg_position_rank=("position_rank", "mean"),
        )
    )

    out = grouped.merge(recent_grouped, on="sleeper_id", how="left")

    out["career_fantasy_ppg_ppr"] = (
        out["career_fantasy_points_ppr"] / out["career_games"].replace(0, pd.NA)
    ).fillna(0)

    out["years_since_first_season"] = latest_season - out["first_season"] + 1
    out["years_since_latest_season"] = latest_season - out["latest_season"]

    out["production_stability_score"] = (
        out["recent_avg_ppg_ppr"].fillna(0) * 3
        + out["avg_fantasy_ppg_ppr"].fillna(0) * 2
        + out["best_fantasy_ppg_ppr"].fillna(0)
        - out["years_since_latest_season"] * 5
    ).clip(lower=0)

    out["career_feature_score"] = (
        out["production_stability_score"]
        + out["seasons_played"] * 2
        + out["recent_games"].fillna(0) * 0.25
        - out["recent_avg_position_rank"].fillna(999) * 0.15
    ).clip(lower=0)

    out = out.round(2)

    # Supabase/PostgREST rejects NaN/inf values in JSON payloads.
    out = out.replace([float("inf"), float("-inf")], None)
    out = out.astype(object).where(pd.notnull(out), None)

    print(f"Built career feature rows: {len(out)}")

    records = out.to_dict("records")

    if records:
        sb.table(TARGET_TABLE).upsert(
            records,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(records)} player career feature rows.")

    return out


if __name__ == "__main__":
    build_player_career_features()
