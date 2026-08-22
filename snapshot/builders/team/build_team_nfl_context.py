from __future__ import annotations

from datetime import datetime, timezone
import math
import pandas as pd

from auth import service_client


TARGET_TABLE = "team_nfl_context"
LOOKBACK_SEASONS = 3


def clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, float(v)))


def percentile_score(series: pd.Series, value, inverse=False):
    if value is None or pd.isna(value):
        return 50.0
    rank = series.rank(pct=True).loc[series.index[series == value][0]] * 100
    return round(100 - rank if inverse else rank, 2)


def main():
    import nfl_data_py as nfl

    sb = service_client()

    current_year = datetime.now(timezone.utc).year
    available = []

    # Try latest years first. Skip years that nflverse has not published yet.
    for year in range(current_year, 1998, -1):
        try:
            test = nfl.import_weekly_data([year], downcast=True)
            if not test.empty:
                available.append(year)
                print(f"Found available weekly data: {year}")
            if len(available) >= LOOKBACK_SEASONS:
                break
        except Exception as e:
            print(f"Skipping unavailable weekly data year {year}: {type(e).__name__}")

    if not available:
        raise RuntimeError("No available nfl_data_py weekly seasons found.")

    SEASONS = sorted(available)
    print(f"Loading weekly NFL data: {SEASONS}")
    weekly = nfl.import_weekly_data(SEASONS, downcast=True)

    weekly = weekly[weekly["recent_team"].notna()].copy()
    weekly["team"] = weekly["recent_team"].replace({"LA": "LAR"})

    for col in [
        "passing_yards", "passing_tds", "interceptions",
        "rushing_yards", "rushing_tds", "carries",
        "attempts", "completions", "receiving_yards",
        "receiving_tds", "targets", "receptions",
        "sacks", "sack_yards", "rushing_fumbles_lost",
        "receiving_fumbles_lost"
    ]:
        if col not in weekly.columns:
            weekly[col] = 0

    grouped = (
        weekly.groupby(["season", "week", "team"], as_index=False)
        .agg(
            pass_yards=("passing_yards", "sum"),
            pass_tds=("passing_tds", "sum"),
            interceptions=("interceptions", "sum"),
            rush_yards=("rushing_yards", "sum"),
            rush_tds=("rushing_tds", "sum"),
            carries=("carries", "sum"),
            attempts=("attempts", "sum"),
            completions=("completions", "sum"),
            rec_yards=("receiving_yards", "sum"),
            rec_tds=("receiving_tds", "sum"),
            targets=("targets", "sum"),
            receptions=("receptions", "sum"),
            rush_fumbles_lost=("rushing_fumbles_lost", "sum"),
            rec_fumbles_lost=("receiving_fumbles_lost", "sum"),
        )
    )

    grouped["plays"] = grouped["attempts"] + grouped["carries"]
    grouped["yards"] = grouped["pass_yards"] + grouped["rush_yards"]
    grouped["tds"] = grouped["pass_tds"] + grouped["rush_tds"]
    grouped["turnovers"] = (
        grouped["interceptions"]
        + grouped["rush_fumbles_lost"]
        + grouped["rec_fumbles_lost"]
    )

    team = (
        grouped.groupby("team", as_index=False)
        .agg(
            games=("week", "count"),
            plays=("plays", "sum"),
            yards=("yards", "sum"),
            pass_yards=("pass_yards", "sum"),
            rush_yards=("rush_yards", "sum"),
            pass_tds=("pass_tds", "sum"),
            rush_tds=("rush_tds", "sum"),
            turnovers=("turnovers", "sum"),
            attempts=("attempts", "sum"),
            carries=("carries", "sum"),
        )
    )

    team["plays_per_game"] = team["plays"] / team["games"]
    team["yards_per_game"] = team["yards"] / team["games"]
    team["pass_yards_per_game"] = team["pass_yards"] / team["games"]
    team["rush_yards_per_game"] = team["rush_yards"] / team["games"]
    team["pass_td_per_game"] = team["pass_tds"] / team["games"]
    team["rush_td_per_game"] = team["rush_tds"] / team["games"]
    team["turnovers_per_game"] = team["turnovers"] / team["games"]
    team["pass_rate"] = team["attempts"] / (team["attempts"] + team["carries"])
    team["rush_rate"] = team["carries"] / (team["attempts"] + team["carries"])

    # Approx fantasy/offensive environment scores.
    team["pace_score"] = team["plays_per_game"].rank(pct=True) * 100
    team["yardage_score"] = team["yards_per_game"].rank(pct=True) * 100
    team["pass_env_score"] = (
        team["pass_yards_per_game"].rank(pct=True) * 60
        + team["pass_td_per_game"].rank(pct=True) * 40
    )
    team["rush_env_score"] = (
        team["rush_yards_per_game"].rank(pct=True) * 60
        + team["rush_td_per_game"].rank(pct=True) * 40
    )
    team["turnover_score"] = 100 - (team["turnovers_per_game"].rank(pct=True) * 100)

    rows = []

    for _, r in team.iterrows():
        pass_rate = float(r["pass_rate"] or 0)
        rush_rate = float(r["rush_rate"] or 0)

        if pass_rate >= 0.60:
            scheme = "PASS_HEAVY"
        elif rush_rate >= 0.45:
            scheme = "RUN_HEAVY"
        else:
            scheme = "BALANCED"

        offensive_environment_score = clamp(
            float(r["yardage_score"]) * 0.35
            + float(r["pace_score"]) * 0.25
            + float(r["pass_env_score"]) * 0.20
            + float(r["rush_env_score"]) * 0.10
            + float(r["turnover_score"]) * 0.10
        )

        qb_environment_score = clamp(
            float(r["pass_env_score"]) * 0.65
            + float(r["pace_score"]) * 0.20
            + float(r["turnover_score"]) * 0.15
        )

        rushing_environment_score = clamp(
            float(r["rush_env_score"]) * 0.70
            + float(r["pace_score"]) * 0.20
            + float(r["yardage_score"]) * 0.10
        )

        rows.append({
            "nfl_team": r["team"],
            "seasons": SEASONS,
            "games": clean(round(float(r["games"]), 2)),
            "plays_per_game": clean(round(float(r["plays_per_game"]), 2)),
            "points_per_game": None,
            "yards_per_game": clean(round(float(r["yards_per_game"]), 2)),
            "pass_rate": clean(round(pass_rate, 3)),
            "rush_rate": clean(round(rush_rate, 3)),
            "pass_yards_per_game": clean(round(float(r["pass_yards_per_game"]), 2)),
            "rush_yards_per_game": clean(round(float(r["rush_yards_per_game"]), 2)),
            "pass_td_per_game": clean(round(float(r["pass_td_per_game"]), 2)),
            "rush_td_per_game": clean(round(float(r["rush_td_per_game"]), 2)),
            "turnovers_per_game": clean(round(float(r["turnovers_per_game"]), 2)),
            "offensive_environment_score": clean(round(offensive_environment_score, 2)),
            "qb_environment_score": clean(round(qb_environment_score, 2)),
            "rushing_environment_score": clean(round(rushing_environment_score, 2)),
            "pace_score": clean(round(float(r["pace_score"]), 2)),
            "scheme_label": scheme,
            "source": "nfl_data_py_weekly",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })

    print(f"Prepared team context rows: {len(rows)}")

    sb.table(TARGET_TABLE).upsert(
        rows,
        on_conflict="nfl_team",
    ).execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}")


if __name__ == "__main__":
    main()
