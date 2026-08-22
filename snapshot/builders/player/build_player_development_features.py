from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import nfl_data_py as nfl

from auth import service_client


CURRENT_YEAR = datetime.now().year


def num(s, default=0):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def pick_col(df, options, default=None):
    for c in options:
        if c in df.columns:
            return c
    return default


def fetch_all(sb, table, select="*"):
    rows = []
    start = 0
    step = 1000

    while True:
        batch = (
            sb.table(table)
            .select(select)
            .range(start, start + step - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)

        if len(batch) < step:
            break

        start += step

    return pd.DataFrame(rows)


def safe_cols(df, cols):
    return [c for c in cols if c in df.columns]


def load_player_metadata():
    players = nfl.import_players()
    out = players.copy()

    sleeper_col = pick_col(out, ["sleeper_id"])
    gsis_col = pick_col(out, ["gsis_id", "player_id"])
    birth_col = pick_col(out, ["birth_date", "birthdate", "dob"])
    name_col = pick_col(out, ["display_name", "player_name", "full_name", "name"])

    out["sleeper_id_meta"] = out[sleeper_col].astype(str) if sleeper_col else None
    out["gsis_id_meta"] = out[gsis_col].astype(str) if gsis_col else None
    out["birth_date"] = pd.to_datetime(out[birth_col], errors="coerce") if birth_col else pd.NaT
    out["metadata_name"] = out[name_col] if name_col else None

    return out[[
        "sleeper_id_meta",
        "gsis_id_meta",
        "metadata_name",
        "birth_date",
    ]].drop_duplicates()


def infer_age_from_birthdate(season, birth_date):
    if pd.isna(birth_date):
        return np.nan
    return int(season) - int(birth_date.year)


def classify_role(row):
    pos = row["pos"]
    games = float(row.get("current_games", 0) or 0)
    ppg = float(row.get("current_ppg", 0) or 0)
    opp = float(row.get("current_opportunities", 0) or 0)
    targets = float(row.get("current_targets", 0) or 0)
    carries = float(row.get("current_carries", 0) or 0)

    if pos == "QB":
        if games >= 12 and ppg >= 17:
            return "FRANCHISE_STARTER"
        if games >= 8 and ppg >= 14:
            return "STARTING_QB"
        if 4 <= games < 8 and ppg >= 14:
            return "SPOT_STARTER_VALUE"
        if 4 <= games < 8:
            return "SYSTEM_BACKUP"
        if games > 0:
            return "DEPTH_BACKUP"
        return "UNKNOWN_QB"

    if pos == "RB":
        if opp >= 250:
            return "WORKHORSE_RB"
        if opp >= 170:
            return "LEAD_RB"
        if targets >= 50 and carries < 150:
            return "PASS_CATCHING_RB"
        if opp >= 80:
            return "COMMITTEE_RB"
        if opp > 0:
            return "DEPTH_RB"
        return "UNKNOWN_RB"

    if pos == "WR":
        if targets >= 120:
            return "ALPHA_WR"
        if targets >= 90:
            return "STARTING_WR"
        if targets >= 55:
            return "ROTATIONAL_WR"
        if targets > 0:
            return "DEPTH_WR"
        return "UNKNOWN_WR"

    if pos == "TE":
        if targets >= 80:
            return "FEATURED_TE"
        if targets >= 50:
            return "STARTING_TE"
        if targets >= 25:
            return "ROTATIONAL_TE"
        if targets > 0:
            return "DEPTH_TE"
        return "UNKNOWN_TE"

    return "UNKNOWN"


def classify_production_tier(row):
    pos = row["pos"]
    ppg = float(row.get("current_ppg", 0) or 0)

    if pos == "QB":
        if ppg >= 20:
            return "ELITE"
        if ppg >= 17:
            return "HIGH_END_STARTER"
        if ppg >= 14:
            return "STARTER"
        if ppg >= 10:
            return "LOW_END_STARTER"
        return "REPLACEMENT"

    if pos in ["RB", "WR"]:
        if ppg >= 18:
            return "ELITE"
        if ppg >= 15:
            return "HIGH_END_STARTER"
        if ppg >= 12:
            return "STARTER"
        if ppg >= 8:
            return "FLEX"
        return "REPLACEMENT"

    if pos == "TE":
        if ppg >= 14:
            return "ELITE"
        if ppg >= 10:
            return "HIGH_END_STARTER"
        if ppg >= 7:
            return "STARTER"
        if ppg >= 5:
            return "STREAMER"
        return "REPLACEMENT"

    return "UNKNOWN"


def retirement_probability(pos, age, career_year, role):
    age = float(age or 0)
    career_year = float(career_year or 0)

    base = 0.03

    if "BACKUP" in role:
        base += 0.05

    if pos == "RB":
        base += max(age - 28, 0) * 0.05
        base += max(career_year - 8, 0) * 0.04
    elif pos == "WR":
        base += max(age - 31, 0) * 0.035
        base += max(career_year - 10, 0) * 0.03
    elif pos == "TE":
        base += max(age - 32, 0) * 0.03
        base += max(career_year - 11, 0) * 0.025
    elif pos == "QB":
        base += max(age - 36, 0) * 0.035
        base += max(career_year - 14, 0) * 0.025

    return round(min(max(base, 0), 0.90), 3)


def classify_trajectory(row):
    pos = row["pos"]
    age = float(row.get("age", 0) or 0)
    current_ppg = float(row.get("current_ppg", 0) or 0)
    expected = float(row.get("expected_ppg_next", 0) or 0)
    delta = expected - current_ppg
    role = row.get("role_archetype", "")
    tier = row.get("production_tier", "")

    if role == "SPOT_STARTER_VALUE":
        return "SPOT_STARTER_VALUE"

    if delta >= 0.60 and tier in ["REPLACEMENT", "FLEX", "STREAMER", "LOW_END_STARTER", "STARTER"]:
        return "ASCENDING"

    if delta >= 1.00:
        return "ASCENDING"

    if delta <= -2.00:
        return "CLIFF"

    if delta <= -0.75:
        return "DECLINING"

    if pos == "RB" and age >= 28:
        return "DECLINE_RISK"

    if pos == "WR" and age >= 31:
        return "DECLINE_RISK"

    if pos == "TE" and age >= 32:
        return "DECLINE_RISK"

    if pos == "QB" and age >= 36:
        return "LATE_CAREER_STABLE"

    if tier in ["ELITE", "HIGH_END_STARTER"]:
        return "PEAK"

    return "STABLE"


def build_player_development_features():
    sb = service_client()

    print("Loading player season stats...")
    season_stats = fetch_all(sb, "player_season_stats")

    print("Loading career curves...")
    career_curves = fetch_all(sb, "historical_age_curve_features")

    print("Loading age curves...")
    age_curves = fetch_all(sb, "historical_player_age_curve_features")

    print("Loading metadata...")
    metadata = load_player_metadata()

    print("Season stat columns:")
    print(sorted(season_stats.columns.tolist()))

    id_col = pick_col(season_stats, ["sleeper_id", "sleeper_player_id", "player_id"])
    gsis_col = pick_col(season_stats, ["gsis_id", "player_gsis_id"])
    name_col = pick_col(season_stats, ["player_name", "player_display_name", "name"])
    pos_col = pick_col(season_stats, ["pos", "position"])
    season_col = pick_col(season_stats, ["season"])

    points_col = pick_col(season_stats, ["fantasy_points_ppr", "fantasy_points", "points_ppr"])
    games_col = pick_col(season_stats, ["games", "games_played"])
    ppg_col = pick_col(season_stats, ["fantasy_ppg_ppr", "fantasy_ppg", "ppg_ppr"])

    targets_col = pick_col(season_stats, ["targets"])
    carries_col = pick_col(season_stats, ["carries"])
    receptions_col = pick_col(season_stats, ["receptions"])
    touches_col = pick_col(season_stats, ["touches"])
    opp_col = pick_col(season_stats, ["opportunities"])
    ypt_col = pick_col(season_stats, ["yards_per_touch"])

    rush_yards_col = pick_col(season_stats, ["rushing_yards"])
    rec_yards_col = pick_col(season_stats, ["receiving_yards"])

    age_col = pick_col(season_stats, ["age", "player_age"])

    missing = [
        name for name, value in {
            "id_col": id_col,
            "name_col": name_col,
            "pos_col": pos_col,
            "season_col": season_col,
        }.items()
        if not value
    ]

    if missing:
        raise RuntimeError(f"Missing required columns: {missing}")

    df = season_stats.copy()

    df["player_id"] = df[id_col].astype(str)
    df["sleeper_id"] = df[id_col].astype(str)
    df["gsis_id"] = df[gsis_col].astype(str) if gsis_col else None
    df["player_name"] = df[name_col]
    df["pos"] = df[pos_col].astype(str)
    df["season"] = num(df[season_col]).astype(int)

    df = df[df["pos"].isin(["QB", "RB", "WR", "TE"])].copy()

    df["current_fantasy_points"] = num(df[points_col]) if points_col else 0
    df["current_games"] = num(df[games_col]) if games_col else 0

    if ppg_col:
        df["current_ppg"] = num(df[ppg_col])
    else:
        df["current_ppg"] = np.where(
            df["current_games"] > 0,
            df["current_fantasy_points"] / df["current_games"],
            0,
        )

    df["current_targets"] = num(df[targets_col]) if targets_col else 0
    df["current_carries"] = num(df[carries_col]) if carries_col else 0
    df["current_receptions"] = num(df[receptions_col]) if receptions_col else 0

    if opp_col:
        df["current_opportunities"] = num(df[opp_col])
    else:
        df["current_opportunities"] = df["current_targets"] + df["current_carries"]

    if touches_col:
        df["current_touches"] = num(df[touches_col])
    else:
        df["current_touches"] = df["current_receptions"] + df["current_carries"]

    if ypt_col:
        df["current_yards_per_touch"] = num(df[ypt_col])
    elif rush_yards_col or rec_yards_col:
        rush_yards = num(df[rush_yards_col]) if rush_yards_col else 0
        rec_yards = num(df[rec_yards_col]) if rec_yards_col else 0
        total_yards = rush_yards + rec_yards
        df["current_yards_per_touch"] = np.where(
            df["current_touches"] > 0,
            total_yards / df["current_touches"],
            0,
        )
    else:
        df["current_yards_per_touch"] = 0

    df = df.sort_values(["player_id", "season"])

    df["career_year"] = (
        df.groupby("player_id")["season"]
        .rank(method="dense")
        .astype(int)
    )

    latest_season = int(df["season"].max())
    current = df[df["season"] == latest_season].copy()

    current = current.merge(
        metadata,
        left_on="sleeper_id",
        right_on="sleeper_id_meta",
        how="left",
    )

    if current["birth_date"].isna().mean() > 0.75 and "gsis_id" in current.columns:
        current = current.drop(columns=["birth_date", "metadata_name", "sleeper_id_meta", "gsis_id_meta"], errors="ignore")
        current = current.merge(
            metadata,
            left_on="gsis_id",
            right_on="gsis_id_meta",
            how="left",
        )

    if age_col:
        current["age"] = num(current[age_col])
        current["age_source"] = "player_season_stats"
    else:
        current["age"] = current.apply(
            lambda r: infer_age_from_birthdate(r["season"], r.get("birth_date")),
            axis=1,
        )
        current["age_source"] = "nfl_data_py_metadata"

    current["age"] = num(current["age"])
    current["age"] = pd.to_numeric(current["age"], errors="coerce")

    current["data_quality_flag"] = np.where(
        current["age"].isna() | (current["age"] <= 0),
        "MISSING_AGE",
        "OK",
    )

    current["age"] = num(current["age"]).astype(int)

    career_curves = career_curves.sort_values(["pos", "career_year"]).copy()
    career_curves["career_delta_ppg"] = (
        career_curves.groupby("pos")["avg_fantasy_ppg_ppr"].diff().fillna(0)
    )

    career_curves = career_curves.rename(
        columns={
            "avg_fantasy_ppg_ppr": "historical_career_ppg",
            "elite_rate_15_ppg": "career_elite_rate",
        }
    )

    age_curves = age_curves.rename(
        columns={
            "avg_fantasy_ppg_ppr": "historical_age_ppg",
            "delta_avg_fantasy_ppg_ppr": "age_delta_ppg",
            "delta_avg_opportunities": "age_delta_opportunities",
            "delta_avg_yards_per_touch": "age_delta_efficiency",
            "elite_rate_15_ppg": "age_elite_rate",
        }
    )

    current = current.merge(
        career_curves[[
            "pos",
            "career_year",
            "historical_career_ppg",
            "career_delta_ppg",
            "career_elite_rate",
        ]],
        on=["pos", "career_year"],
        how="left",
    )

    current = current.merge(
        age_curves[[
            "pos",
            "age",
            "historical_age_ppg",
            "age_delta_ppg",
            "age_delta_opportunities",
            "age_delta_efficiency",
            "age_elite_rate",
        ]],
        on=["pos", "age"],
        how="left",
    )

    for c in [
        "historical_career_ppg",
        "career_delta_ppg",
        "career_elite_rate",
        "historical_age_ppg",
        "age_delta_ppg",
        "age_delta_opportunities",
        "age_delta_efficiency",
        "age_elite_rate",
    ]:
        current[c] = num(current[c])

    current["role_archetype"] = current.apply(classify_role, axis=1)
    current["archetype"] = current["role_archetype"]
    current["production_tier"] = current.apply(classify_production_tier, axis=1)

    current["expected_ppg_next"] = (
        current["current_ppg"]
        + (current["age_delta_ppg"] * 0.45)
        + (current["career_delta_ppg"] * 0.35)
    )

    current["expected_ppg_next"] = np.where(
        current["role_archetype"].eq("SPOT_STARTER_VALUE"),
        current["expected_ppg_next"] * 0.95,
        current["expected_ppg_next"],
    )

    current["expected_opportunity_change"] = current["age_delta_opportunities"]
    current["expected_efficiency_change"] = current["age_delta_efficiency"]

    production_bonus = np.select(
        [
            current["production_tier"].eq("ELITE"),
            current["production_tier"].eq("HIGH_END_STARTER"),
            current["production_tier"].eq("STARTER"),
            current["production_tier"].isin(["FLEX", "LOW_END_STARTER", "STREAMER"]),
        ],
        [0.55, 0.38, 0.22, 0.10],
        default=0.03,
    )

    current["elite_probability"] = (
        production_bonus * 0.65
        + current["age_elite_rate"] * 0.20
        + current["career_elite_rate"] * 0.15
    )

    current["breakout_probability"] = np.where(
        current["current_ppg"] < 12,
        np.clip((current["expected_ppg_next"] - current["current_ppg"]) / 5, 0, 0.60),
        np.clip((current["expected_ppg_next"] - current["current_ppg"]) / 8, 0, 0.35),
    )

    current["decline_probability"] = np.clip(
        (current["current_ppg"] - current["expected_ppg_next"]) / 6,
        0,
        0.75,
    )

    current["replacement_probability"] = np.select(
        [
            current["production_tier"].eq("REPLACEMENT"),
            current["production_tier"].isin(["FLEX", "STREAMER", "LOW_END_STARTER"]),
            current["production_tier"].eq("STARTER"),
        ],
        [0.55, 0.32, 0.18],
        default=0.08,
    )

    current["retirement_probability"] = current.apply(
        lambda r: retirement_probability(
            r["pos"],
            r["age"],
            r["career_year"],
            r["role_archetype"],
        ),
        axis=1,
    )

    current["trajectory"] = current.apply(classify_trajectory, axis=1)

    current["development_score"] = (
        np.clip(current["current_ppg"] / 22, 0, 1) * 35
        + np.clip(current["expected_ppg_next"] / 22, 0, 1) * 25
        + np.clip(current["elite_probability"], 0, 1) * 20
        + np.clip(current["breakout_probability"], 0, 1) * 10
        + (1 - np.clip(current["retirement_probability"], 0, 1)) * 10
    )

    now = datetime.now(timezone.utc).isoformat()

    final = current[[
        "player_id",
        "sleeper_id",
        "player_name",
        "pos",
        "age",
        "career_year",
        "archetype",
        "role_archetype",
        "production_tier",
        "trajectory",
        "season",
        "current_games",
        "current_ppg",
        "current_fantasy_points",
        "current_opportunities",
        "current_touches",
        "current_targets",
        "current_carries",
        "current_yards_per_touch",
        "historical_age_ppg",
        "historical_career_ppg",
        "age_delta_ppg",
        "career_delta_ppg",
        "expected_ppg_next",
        "expected_opportunity_change",
        "expected_efficiency_change",
        "breakout_probability",
        "elite_probability",
        "decline_probability",
        "replacement_probability",
        "retirement_probability",
        "development_score",
        "age_source",
        "data_quality_flag",
    ]].copy()

    final = final.rename(columns={"season": "current_season"})
    final["updated_at"] = now

    numeric_cols = [
        "current_games",
        "current_ppg",
        "current_fantasy_points",
        "current_opportunities",
        "current_touches",
        "current_targets",
        "current_carries",
        "current_yards_per_touch",
        "historical_age_ppg",
        "historical_career_ppg",
        "age_delta_ppg",
        "career_delta_ppg",
        "expected_ppg_next",
        "expected_opportunity_change",
        "expected_efficiency_change",
        "breakout_probability",
        "elite_probability",
        "decline_probability",
        "replacement_probability",
        "retirement_probability",
        "development_score",
    ]

    for c in numeric_cols:
        final[c] = num(final[c]).round(3)

    final["age"] = num(final["age"]).astype(int)
    final["career_year"] = num(final["career_year"]).astype(int)
    final["current_season"] = num(final["current_season"]).astype(int)

    rows = final.to_dict("records")

    sb.table("player_development_features").upsert(
        rows,
        on_conflict="player_id",
    ).execute()

    print(f"Upserted player development rows: {len(rows)}")

    preview_cols = [
        "player_name",
        "pos",
        "age",
        "career_year",
        "role_archetype",
        "production_tier",
        "trajectory",
        "current_games",
        "current_ppg",
        "current_targets",
        "current_carries",
        "current_opportunities",
        "expected_ppg_next",
        "elite_probability",
        "development_score",
        "data_quality_flag",
    ]

    print(
        final.sort_values("development_score", ascending=False)
        [preview_cols]
        .head(80)
        .to_string(index=False)
    )

    return final


if __name__ == "__main__":
    build_player_development_features()
