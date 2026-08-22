from __future__ import annotations

from datetime import datetime, timezone
import math
import pandas as pd

from auth import service_client


TARGET_TABLE = "offensive_line_context"
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


def main():
    import nfl_data_py as nfl

    sb = service_client()

    current_year = datetime.now(timezone.utc).year
    available = []

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
        raise RuntimeError("No available weekly data found.")

    seasons = sorted(available)
    print(f"Loading weekly data: {seasons}")
    weekly = nfl.import_weekly_data(seasons, downcast=True)

    weekly = weekly[weekly["recent_team"].notna()].copy()
    weekly["team"] = weekly["recent_team"].replace({"LA": "LAR"})

    for col in [
        "attempts",
        "sacks",
        "carries",
        "rushing_yards",
        "rushing_tds",
    ]:
        if col not in weekly.columns:
            weekly[col] = 0

    team_week = (
        weekly.groupby(["season", "week", "team"], as_index=False)
        .agg(
            attempts=("attempts", "sum"),
            sacks=("sacks", "sum"),
            carries=("carries", "sum"),
            rushing_yards=("rushing_yards", "sum"),
            rushing_tds=("rushing_tds", "sum"),
        )
    )

    team = (
        team_week.groupby("team", as_index=False)
        .agg(
            games=("week", "count"),
            attempts=("attempts", "sum"),
            sacks=("sacks", "sum"),
            carries=("carries", "sum"),
            rushing_yards=("rushing_yards", "sum"),
            rushing_tds=("rushing_tds", "sum"),
        )
    )

    team["sacks_allowed_per_game"] = team["sacks"] / team["games"]
    team["sack_rate_allowed"] = team["sacks"] / (team["attempts"] + team["sacks"])
    team["rushing_yards_per_carry"] = team["rushing_yards"] / team["carries"]
    team["rushing_td_per_game"] = team["rushing_tds"] / team["games"]
    team["rush_volume_per_game"] = team["carries"] / team["games"]

    # Lower sack rate is better.
    team["pass_protection_score"] = 100 - (team["sack_rate_allowed"].rank(pct=True) * 100)

    # Run blocking proxy: efficiency + TD push + volume sustainability.
    team["run_blocking_score"] = (
        team["rushing_yards_per_carry"].rank(pct=True) * 45
        + team["rushing_td_per_game"].rank(pct=True) * 35
        + team["rush_volume_per_game"].rank(pct=True) * 20
    )

    team["offensive_line_score"] = (
        team["pass_protection_score"] * 0.50
        + team["run_blocking_score"] * 0.50
    )

    rows = []
    for _, r in team.iterrows():
        rows.append({
            "nfl_team": r["team"],
            "seasons": seasons,
            "games": clean(round(float(r["games"]), 2)),
            "sacks_allowed_per_game": clean(round(float(r["sacks_allowed_per_game"]), 2)),
            "sack_rate_allowed": clean(round(float(r["sack_rate_allowed"]), 4)),
            "rushing_yards_per_carry": clean(round(float(r["rushing_yards_per_carry"]), 3)),
            "rushing_td_per_game": clean(round(float(r["rushing_td_per_game"]), 2)),
            "rush_volume_per_game": clean(round(float(r["rush_volume_per_game"]), 2)),
            "pass_protection_score": clean(round(clamp(r["pass_protection_score"]), 2)),
            "run_blocking_score": clean(round(clamp(r["run_blocking_score"]), 2)),
            "offensive_line_score": clean(round(clamp(r["offensive_line_score"]), 2)),
            "source": "nfl_data_py_weekly_proxy",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })

    print(f"Prepared offensive line rows: {len(rows)}")

    sb.table(TARGET_TABLE).upsert(
        rows,
        on_conflict="nfl_team",
    ).execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}")


if __name__ == "__main__":
    main()
