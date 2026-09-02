from __future__ import annotations

# ============================================================
# Imports / Path Setup
# ============================================================

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from auth import service_client


# ============================================================
# Config
# ============================================================

SUPPORTED_POSITIONS = {
    "QB",
    "RB",
    "WR",
    "TE",
}

PAGE_SIZE = 1000


# ============================================================
# Helpers
# ============================================================

def num(series, default=0):
    return (
        pd.to_numeric(
            series,
            errors="coerce",
        )
        .fillna(default)
    )


def clean_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    if text.lower() in {
        "nan",
        "none",
        "null",
    }:
        return None

    return text


def safe_int(value: Any) -> int | None:
    try:
        return int(
            float(
                str(value).strip()
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return None


# ============================================================
# Canonical Season Authority
# ============================================================

def resolve_canonical_active_season(
    sb,
) -> tuple[str, int]:
    """
    Resolve the active Legacy league and its canonical season.

    Standalone jobs do not have Streamlit session state, so the
    configured SLEEPER_LEAGUE_ID identifies the correct Legacy
    league.

    Season authority remains:

        league_seasons.is_active = true

    Fail closed if either the league or season is missing or
    ambiguous.
    """

    sleeper_league_id = "".join(
        ch
        for ch in os.getenv(
            "SLEEPER_LEAGUE_ID",
            "",
        ).strip()
        if ch.isdigit()
    )

    if not sleeper_league_id:
        raise RuntimeError(
            "SLEEPER_LEAGUE_ID is not configured."
        )

    league_rows = (
        sb.table("leagues")
        .select(
            "id,sleeper_league_id"
        )
        .eq(
            "sleeper_league_id",
            sleeper_league_id,
        )
        .execute()
        .data
        or []
    )

    if len(league_rows) != 1:
        raise RuntimeError(
            (
                "Expected exactly one Legacy league for "
                f"Sleeper league {sleeper_league_id}; "
                f"found {len(league_rows)}."
            )
        )

    league_id = str(
        league_rows[0]["id"]
    )

    season_rows = (
        sb.table("league_seasons")
        .select(
            "season,is_active"
        )
        .eq(
            "league_id",
            league_id,
        )
        .eq(
            "is_active",
            True,
        )
        .execute()
        .data
        or []
    )

    if len(season_rows) != 1:
        raise RuntimeError(
            (
                "Expected exactly one canonical active season "
                f"for Legacy league {league_id}; "
                f"found {len(season_rows)}."
            )
        )

    season = safe_int(
        season_rows[0].get(
            "season"
        )
    )

    if season is None:
        raise RuntimeError(
            "Canonical active season is missing its season value."
        )

    print(
        f"Resolved Legacy league: {league_id}"
    )

    print(
        f"Configured Sleeper league: {sleeper_league_id}"
    )

    print(
        f"Canonical active league season: {season}"
    )

    return (
        league_id,
        season,
    )


# ============================================================
# Determine Scoring Season
# ============================================================

def weekly_season_exists(
    sb,
    season: int,
) -> bool:
    """
    Return True if player_weekly_stats contains at least one row
    for the requested season.
    """

    rows = (
        sb.table(
            "player_weekly_stats"
        )
        .select(
            "season"
        )
        .eq(
            "season",
            season,
        )
        .limit(1)
        .execute()
        .data
        or []
    )

    return bool(
        rows
    )


def resolve_rollup_season(
    sb,
    active_season: int,
) -> int:
    """
    Determine which scoring season should currently be rolled up.

    During the offseason:

        active league season = 2026
        no 2026 weekly scoring rows
        -> use 2025

    Once the season begins:

        active league season = 2026
        2026 weekly scoring rows exist
        -> use 2026

    No annual code edit is required.
    """

    if weekly_season_exists(
        sb,
        active_season,
    ):
        print(
            (
                f"Weekly scoring exists for active season "
                f"{active_season}."
            )
        )

        print(
            f"Using {active_season} as the season-stat authority."
        )

        return active_season

    previous_season = (
        active_season - 1
    )

    if weekly_season_exists(
        sb,
        previous_season,
    ):
        print(
            (
                f"No weekly scoring exists for active season "
                f"{active_season} yet."
            )
        )

        print(
            (
                f"Using completed season {previous_season} "
                "as the offseason production authority."
            )
        )

        return previous_season

    raise RuntimeError(
        (
            "No player_weekly_stats rows exist for either "
            f"{active_season} or {previous_season}."
        )
    )


# ============================================================
# Load Weekly Stats
# ============================================================

def load_weekly_data(
    sb,
    season: int,
) -> pd.DataFrame:
    """
    Load all player_weekly_stats rows for one season.

    Uses explicit pagination so Supabase's normal API row limit
    cannot silently truncate a season.
    """

    print("")
    print(
        f"Loading player_weekly_stats for {season}..."
    )

    rows: list[dict[str, Any]] = []

    start = 0

    while True:
        page = (
            sb.table(
                "player_weekly_stats"
            )
            .select("*")
            .eq(
                "season",
                season,
            )
            .order(
                "week"
            )
            .order(
                "sleeper_id"
            )
            .range(
                start,
                start + PAGE_SIZE - 1,
            )
            .execute()
            .data
            or []
        )

        rows.extend(
            dict(row)
            for row in page
        )

        if len(page) < PAGE_SIZE:
            break

        start += PAGE_SIZE

    if not rows:
        raise RuntimeError(
            (
                "player_weekly_stats contains no rows "
                f"for season {season}."
            )
        )

    weekly = pd.DataFrame(
        rows
    )

    print(
        f"Loaded weekly rows for {season}: {len(weekly)}"
    )

    if "season_type" in weekly.columns:
        weekly = weekly[
            weekly["season_type"]
            .astype(str)
            .str.upper()
            .eq("REG")
        ].copy()

    print(
        (
            f"Regular-season weekly rows for {season}: "
            f"{len(weekly)}"
        )
    )

    if weekly.empty:
        raise RuntimeError(
            (
                f"No regular-season player_weekly_stats rows "
                f"exist for {season}."
            )
        )

    required_columns = {
        "season",
        "week",
        "sleeper_id",
        "player_name",
        "pos",
        "fantasy_points",
        "fantasy_points_ppr",
    }

    missing = sorted(
        required_columns
        - set(
            weekly.columns
        )
    )

    if missing:
        raise RuntimeError(
            (
                "player_weekly_stats is missing required "
                f"columns: {', '.join(missing)}"
            )
        )

    return weekly


# ============================================================
# Player Style
# ============================================================

def classify_player_style(
    row,
) -> str:
    """
    Preserve the existing style classification when the source
    weekly data contains enough opportunity information.

    If those fields are unavailable, return UNKNOWN instead of
    inventing zero-volume production.
    """

    required_style_fields = {
        "carries",
        "targets",
        "receptions",
        "opportunities",
    }

    if not required_style_fields.issubset(
        set(
            row.index
        )
    ):
        return "UNKNOWN"

    pos = clean_text(
        row.get(
            "pos"
        )
    )

    carries = float(
        row.get(
            "carries",
            0,
        )
        or 0
    )

    targets = float(
        row.get(
            "targets",
            0,
        )
        or 0
    )

    receptions = float(
        row.get(
            "receptions",
            0,
        )
        or 0
    )

    opportunities = float(
        row.get(
            "opportunities",
            0,
        )
        or 0
    )

    ypc = float(
        row.get(
            "yards_per_carry",
            0,
        )
        or 0
    )

    ypt = float(
        row.get(
            "yards_per_target",
            0,
        )
        or 0
    )

    air_yards = float(
        row.get(
            "receiving_air_yards",
            0,
        )
        or 0
    )

    pass_attempts = float(
        row.get(
            "passing_attempts",
            0,
        )
        or 0
    )

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
        if (
            opportunities >= 260
            and targets >= 50
        ):
            return "THREE_DOWN_RB"

        if carries >= 220:
            return "POWER_VOLUME_RB"

        if targets >= 60:
            return "PASS_CATCHING_RB"

        if opportunities >= 140:
            return "COMMITTEE_RB"

        return "DEPTH_RB"

    if pos == "WR":
        if (
            targets >= 120
            and air_yards >= 1200
        ):
            return "ALPHA_VERTICAL_WR"

        if targets >= 120:
            return "ALPHA_VOLUME_WR"

        if (
            targets >= 80
            and ypt >= 9
        ):
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


# ============================================================
# Build Aggregation
# ============================================================

def build_aggregation_spec(
    df: pd.DataFrame,
) -> dict[str, tuple[str, str]]:
    """
    Build the season aggregation from fields actually available
    in player_weekly_stats.

    Core fantasy production is required.

    Optional football-volume fields are aggregated only when they
    exist, rather than fabricating missing statistics as zero.
    """

    spec: dict[
        str,
        tuple[str, str],
    ] = {
        "games": (
            "week",
            "nunique",
        ),
        "fantasy_points": (
            "fantasy_points",
            "sum",
        ),
        "fantasy_points_ppr": (
            "fantasy_points_ppr",
            "sum",
        ),
    }

    if "team" in df.columns:
        spec["team"] = (
            "team",
            "last",
        )

    sum_columns = [
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
    ]

    mean_columns = [
        "target_share",
        "air_yards_share",
        "wopr",
        "racr",
    ]

    for column in sum_columns:
        if column in df.columns:
            spec[column] = (
                column,
                "sum",
            )

    for column in mean_columns:
        if column in df.columns:
            spec[column] = (
                column,
                "mean",
            )

    return spec


# ============================================================
# Build Season Stats
# ============================================================

def build_player_season_stats():
    """
    Build the current production-authority season directly from
    canonical player_weekly_stats.

    This intentionally does not download NFL data itself.

    External ingestion belongs to:

        engine/load_nflverse_weekly_stats.py

    This builder performs only:

        player_weekly_stats
            ->
        player_season_stats
    """

    sb = service_client()

    (
        league_id,
        active_season,
    ) = resolve_canonical_active_season(
        sb
    )

    rollup_season = resolve_rollup_season(
        sb,
        active_season,
    )

    print("")
    print(
        "============================================"
    )

    print(
        "Legacy Player Season Stats Builder"
    )

    print(
        "============================================"
    )

    print(
        f"Legacy league: {league_id}"
    )

    print(
        f"Canonical active season: {active_season}"
    )

    print(
        f"Production season being rebuilt: {rollup_season}"
    )

    weekly = load_weekly_data(
        sb,
        rollup_season,
    )

    df = weekly.copy()

    # --------------------------------------------------------
    # Normalize identifiers
    # --------------------------------------------------------

    df["season"] = pd.to_numeric(
        df["season"],
        errors="coerce",
    )

    df["week"] = pd.to_numeric(
        df["week"],
        errors="coerce",
    )

    df["sleeper_id"] = (
        df["sleeper_id"]
        .astype(str)
        .str.strip()
    )

    df["player_name"] = (
        df["player_name"]
        .astype(str)
        .str.strip()
    )

    df["pos"] = (
        df["pos"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    if "gsis_id" not in df.columns:
        df["gsis_id"] = None

    if "team" not in df.columns:
        df["team"] = None

    # --------------------------------------------------------
    # Remove invalid identity rows
    # --------------------------------------------------------

    df = df[
        df["sleeper_id"].notna()
        & df["sleeper_id"].ne("")
        & df["sleeper_id"].ne("None")
        & df["player_name"].notna()
        & df["player_name"].ne("")
    ].copy()

    # --------------------------------------------------------
    # Fantasy positions only
    # --------------------------------------------------------

    df = df[
        df["pos"].isin(
            SUPPORTED_POSITIONS
        )
    ].copy()

    if df.empty:
        raise RuntimeError(
            (
                "No QB/RB/WR/TE weekly rows remain after "
                "identity and position filtering."
            )
        )

    # --------------------------------------------------------
    # Normalize available numeric columns
    # --------------------------------------------------------

    numeric_columns = [
        "fantasy_points",
        "fantasy_points_ppr",
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
        "target_share",
        "air_yards_share",
        "wopr",
        "racr",
    ]

    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            ).fillna(0)

    # --------------------------------------------------------
    # Build season aggregation
    # --------------------------------------------------------

    aggregation = build_aggregation_spec(
        df
    )

    season_rows = (
        df.groupby(
            [
                "season",
                "sleeper_id",
                "gsis_id",
                "player_name",
                "pos",
            ],
            dropna=False,
        )
        .agg(
            **aggregation
        )
        .reset_index()
    )

    # --------------------------------------------------------
    # PPG
    # --------------------------------------------------------

    season_rows["fantasy_ppg"] = (
        season_rows[
            "fantasy_points"
        ]
        / season_rows[
            "games"
        ].replace(
            0,
            pd.NA,
        )
    ).fillna(0)

    season_rows["fantasy_ppg_ppr"] = (
        season_rows[
            "fantasy_points_ppr"
        ]
        / season_rows[
            "games"
        ].replace(
            0,
            pd.NA,
        )
    ).fillna(0)

    # --------------------------------------------------------
    # Optional opportunity calculations
    # --------------------------------------------------------

    if {
        "carries",
        "receptions",
    }.issubset(
        season_rows.columns
    ):
        season_rows["touches"] = (
            season_rows["carries"]
            + season_rows["receptions"]
        )

    if {
        "carries",
        "targets",
    }.issubset(
        season_rows.columns
    ):
        season_rows["opportunities"] = (
            season_rows["carries"]
            + season_rows["targets"]
        )

    if {
        "touches",
        "rushing_yards",
        "receiving_yards",
    }.issubset(
        season_rows.columns
    ):
        denominator = (
            season_rows[
                "touches"
            ].replace(
                0,
                pd.NA,
            )
        )

        season_rows["yards_per_touch"] = (
            (
                season_rows[
                    "rushing_yards"
                ]
                + season_rows[
                    "receiving_yards"
                ]
            )
            / denominator
        ).fillna(0)

    if {
        "targets",
        "receiving_yards",
    }.issubset(
        season_rows.columns
    ):
        denominator = (
            season_rows[
                "targets"
            ].replace(
                0,
                pd.NA,
            )
        )

        season_rows["yards_per_target"] = (
            season_rows[
                "receiving_yards"
            ]
            / denominator
        ).fillna(0)

    if {
        "carries",
        "rushing_yards",
    }.issubset(
        season_rows.columns
    ):
        denominator = (
            season_rows[
                "carries"
            ].replace(
                0,
                pd.NA,
            )
        )

        season_rows["yards_per_carry"] = (
            season_rows[
                "rushing_yards"
            ]
            / denominator
        ).fillna(0)

    if {
        "targets",
        "receptions",
    }.issubset(
        season_rows.columns
    ):
        denominator = (
            season_rows[
                "targets"
            ].replace(
                0,
                pd.NA,
            )
        )

        season_rows["catch_rate"] = (
            season_rows[
                "receptions"
            ]
            / denominator
        ).fillna(0)

    if {
        "passing_attempts",
        "completions",
    }.issubset(
        season_rows.columns
    ):
        denominator = (
            season_rows[
                "passing_attempts"
            ].replace(
                0,
                pd.NA,
            )
        )

        season_rows["completion_rate"] = (
            season_rows[
                "completions"
            ]
            / denominator
        ).fillna(0)

    if {
        "targets",
        "receiving_first_downs",
    }.issubset(
        season_rows.columns
    ):
        denominator = (
            season_rows[
                "targets"
            ].replace(
                0,
                pd.NA,
            )
        )

        season_rows[
            "receiving_first_down_rate"
        ] = (
            season_rows[
                "receiving_first_downs"
            ]
            / denominator
        ).fillna(0)

    if {
        "carries",
        "rushing_first_downs",
    }.issubset(
        season_rows.columns
    ):
        denominator = (
            season_rows[
                "carries"
            ].replace(
                0,
                pd.NA,
            )
        )

        season_rows[
            "rushing_first_down_rate"
        ] = (
            season_rows[
                "rushing_first_downs"
            ]
            / denominator
        ).fillna(0)

    # --------------------------------------------------------
    # Player Style
    # --------------------------------------------------------

    style_source_fields = {
        "carries",
        "targets",
        "receptions",
        "opportunities",
    }

    if style_source_fields.issubset(
        season_rows.columns
    ):
        season_rows[
            "player_style"
        ] = season_rows.apply(
            classify_player_style,
            axis=1,
        )

    else:
        season_rows[
            "player_style"
        ] = "UNKNOWN"

    # --------------------------------------------------------
    # Rankings
    # --------------------------------------------------------

    season_rows[
        "position_rank"
    ] = (
        season_rows.groupby(
            [
                "season",
                "pos",
            ]
        )[
            "fantasy_points_ppr"
        ]
        .rank(
            method="first",
            ascending=False,
        )
        .astype(int)
    )

    season_rows[
        "overall_rank"
    ] = (
        season_rows.groupby(
            [
                "season",
            ]
        )[
            "fantasy_points_ppr"
        ]
        .rank(
            method="first",
            ascending=False,
        )
        .astype(int)
    )

    # --------------------------------------------------------
    # Provenance
    # --------------------------------------------------------

    season_rows[
        "source"
    ] = "player_weekly_stats_rollup"

    season_rows[
        "updated_at"
    ] = datetime.now(
        timezone.utc
    ).isoformat()

    # --------------------------------------------------------
    # Round numeric output
    # --------------------------------------------------------

    numeric_round_columns = [
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

    for column in numeric_round_columns:
        if column in season_rows.columns:
            season_rows[
                column
            ] = pd.to_numeric(
                season_rows[
                    column
                ],
                errors="coerce",
            ).round(3)

    # --------------------------------------------------------
    # Final cleanup
    # --------------------------------------------------------

    season_rows[
        "season"
    ] = season_rows[
        "season"
    ].astype(int)

    season_rows[
        "games"
    ] = season_rows[
        "games"
    ].astype(int)

    season_rows = (
        season_rows
        .where(
            pd.notnull(
                season_rows
            ),
            None,
        )
    )

    rows = season_rows.to_dict(
        orient="records"
    )

    print("")
    print(
        f"Prepared season rows: {len(rows)}"
    )

    if not rows:
        raise RuntimeError(
            "No player season rows were prepared."
        )

    # --------------------------------------------------------
    # Upsert
    # --------------------------------------------------------

    (
        sb.table(
            "player_season_stats"
        )
        .upsert(
            rows,
            on_conflict=(
                "season,sleeper_id"
            ),
        )
        .execute()
    )

    print(
        (
            "Upserted player season stat rows: "
            f"{len(rows)}"
        )
    )

    print("")
    print(
        season_rows[
            [
                column
                for column in [
                    "season",
                    "sleeper_id",
                    "player_name",
                    "pos",
                    "team",
                    "games",
                    "fantasy_points_ppr",
                    "fantasy_ppg_ppr",
                    "position_rank",
                    "overall_rank",
                ]
                if column
                in season_rows.columns
            ]
        ]
        .sort_values(
            "fantasy_ppg_ppr",
            ascending=False,
        )
        .head(40)
        .to_string(
            index=False
        )
    )

    print("")
    print(
        "============================================"
    )

    print(
        "Season stats build complete"
    )

    print(
        "============================================"
    )

    print(
        f"Canonical league season: {active_season}"
    )

    print(
        f"Production season rebuilt: {rollup_season}"
    )

    print(
        f"Season rows upserted: {len(rows)}"
    )

    if (
        rollup_season
        < active_season
    ):
        print(
            (
                "Mode: offseason fallback — "
                f"{rollup_season} remains the production "
                f"authority until {active_season} weekly "
                "scoring becomes available."
            )
        )

    else:
        print(
            (
                "Mode: live season — "
                f"{active_season} weekly scoring is active."
            )
        )

    return season_rows


# ============================================================
# Entrypoint
# ============================================================

if __name__ == "__main__":
    build_player_season_stats()