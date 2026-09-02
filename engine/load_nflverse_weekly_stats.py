from __future__ import annotations

# ============================================================
# Imports / Path Setup
# ============================================================

import sys
from pathlib import Path
from urllib.error import HTTPError

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth import service_client


# ============================================================
# Config
# ============================================================

NFLVERSE_WEEKLY_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)


# ============================================================
# Helpers
# ============================================================

def val(row, col, default=0):
    value = row.get(col, default)

    if pd.isna(value):
        return default

    return value


def build_weekly_url(season: int) -> str:
    return NFLVERSE_WEEKLY_URL_TEMPLATE.format(
        season=season
    )


def resolve_canonical_active_season(sb) -> int:
    """
    Resolve the canonical active season for the Legacy league
    linked to the configured Sleeper league.

    Standalone jobs do not have access to Streamlit session state,
    so SLEEPER_LEAGUE_ID is used to identify the correct Legacy
    league before resolving league_seasons.is_active=true.

    Fail closed if league or season authority is missing or
    ambiguous.
    """

    import os

    sleeper_league_id = "".join(
        ch
        for ch in os.getenv("SLEEPER_LEAGUE_ID", "").strip()
        if ch.isdigit()
    )

    if not sleeper_league_id:
        raise RuntimeError(
            "SLEEPER_LEAGUE_ID is not configured."
        )

    league_rows = (
        sb.table("leagues")
        .select("id,sleeper_league_id")
        .eq("sleeper_league_id", sleeper_league_id)
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
        .select("season,is_active")
        .eq("league_id", league_id)
        .eq("is_active", True)
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

    season = season_rows[0].get(
        "season"
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

    return int(
        season
    )

def load_nflverse_frame(
    season: int,
) -> pd.DataFrame | None:
    """
    Load the nflverse weekly file for one season.

    Returns None when nflverse has not published the season yet.
    That is expected during the offseason before the new season's
    data becomes available.
    """

    url = build_weekly_url(
        season
    )

    print("")
    print(
        f"Checking nflverse weekly stats for {season}..."
    )
    print(
        f"Source: {url}"
    )

    try:
        weekly = pd.read_csv(
            url,
            low_memory=False,
        )

    except HTTPError as exc:
        if exc.code == 404:
            print(
                f"No nflverse weekly file exists for {season} yet."
            )
            return None

        raise

    except Exception as exc:
        message = str(exc)

        if "404" in message:
            print(
                f"No nflverse weekly file exists for {season} yet."
            )
            return None

        raise

    if weekly.empty:
        print(
            f"NFLverse weekly file for {season} is empty."
        )
        return None

    return weekly


# ============================================================
# Load Bridge
# ============================================================

def load_identity_bridge(sb) -> pd.DataFrame:
    print(
        "Loading identity bridge..."
    )

    bridge = (
        sb.table("player_identity_bridge")
        .select(
            "canonical_player_id,"
            "sleeper_id,"
            "gsis_id,"
            "player_name,"
            "pos,"
            "team"
        )
        .execute()
        .data
        or []
    )

    bridge_df = pd.DataFrame(
        bridge
    )

    if bridge_df.empty:
        raise RuntimeError(
            "player_identity_bridge is empty."
        )

    bridge_df["gsis_id"] = (
        bridge_df["gsis_id"]
        .astype(str)
    )

    return bridge_df


# ============================================================
# Resolve Season To Import
# ============================================================

def resolve_import_season(
    active_season: int,
) -> tuple[int, pd.DataFrame]:
    """
    Determine which NFL season should currently feed Legacy.

    Preferred:
        canonical active season

    Offseason fallback:
        previous season

    Example:

        League active season = 2026

        If nflverse has 2026 weekly data:
            import 2026

        If nflverse does not yet have 2026 weekly data:
            import/refresh 2025

    Once the 2026 file exists, the next run automatically
    switches to 2026 without any code change.
    """

    active_weekly = load_nflverse_frame(
        active_season
    )

    if active_weekly is not None:
        print("")
        print(
            f"Active-season scoring is available for {active_season}."
        )
        print(
            f"Using {active_season} as the live scoring season."
        )

        return (
            active_season,
            active_weekly,
        )


    previous_season = (
        active_season - 1
    )

    print("")
    print(
        f"{active_season} scoring is not available yet."
    )
    print(
        f"Falling back to completed season {previous_season}."
    )


    previous_weekly = load_nflverse_frame(
        previous_season
    )

    if previous_weekly is None:
        raise RuntimeError(
            (
                "Neither the canonical active season "
                f"({active_season}) nor previous season "
                f"({previous_season}) is available from nflverse."
            )
        )


    return (
        previous_season,
        previous_weekly,
    )


# ============================================================
# Import One Season
# ============================================================

def import_weekly_frame(
    sb,
    bridge_df: pd.DataFrame,
    season: int,
    weekly: pd.DataFrame,
) -> int:
    """
    Transform one nflverse weekly season and upsert it into
    player_weekly_stats.
    """

    print("")
    print(
        f"Preparing nflverse weekly stats for {season}..."
    )


    if "season_type" in weekly.columns:
        weekly = weekly[
            weekly["season_type"] == "REG"
        ].copy()

    else:
        weekly = weekly.copy()


    if weekly.empty:
        print(
            f"No regular-season rows found for {season}."
        )
        return 0


    if "player_id" not in weekly.columns:
        raise RuntimeError(
            (
                f"NFLverse {season} file is missing "
                "required player_id column."
            )
        )


    weekly["gsis_id"] = (
        weekly["player_id"]
        .astype(str)
    )


    merged = weekly.merge(
        bridge_df,
        on="gsis_id",
        how="inner",
        suffixes=(
            "_nflverse",
            "_bridge",
        ),
    )


    print(
        f"Matched weekly stat rows for {season}: {len(merged)}"
    )


    if merged.empty:
        raise RuntimeError(
            (
                f"No nflverse {season} player rows matched "
                "player_identity_bridge."
            )
        )


    rows = []


    for _, r in merged.iterrows():

        rows.append(
            {
                "season": int(
                    val(
                        r,
                        "season",
                        season,
                    )
                ),

                "week": int(
                    val(
                        r,
                        "week",
                    )
                ),

                "season_type": val(
                    r,
                    "season_type",
                    None,
                ),

                "canonical_player_id": val(
                    r,
                    "canonical_player_id",
                    None,
                ),

                "sleeper_id": val(
                    r,
                    "sleeper_id",
                    None,
                ),

                "gsis_id": val(
                    r,
                    "gsis_id",
                    None,
                ),

                "player_name": (
                    val(
                        r,
                        "player_name_bridge",
                        None,
                    )
                    or val(
                        r,
                        "player_name_nflverse",
                        None,
                    )
                    or val(
                        r,
                        "player_display_name",
                        None,
                    )
                ),

                "pos": (
                    val(
                        r,
                        "pos_bridge",
                        None,
                    )
                    or val(
                        r,
                        "position",
                        None,
                    )
                    or val(
                        r,
                        "pos_nflverse",
                        None,
                    )
                ),

                "team": (
                    val(
                        r,
                        "recent_team",
                        None,
                    )
                    or val(
                        r,
                        "team_nflverse",
                        None,
                    )
                    or val(
                        r,
                        "team_bridge",
                        None,
                    )
                ),

                "opponent": val(
                    r,
                    "opponent_team",
                    None,
                ),

                "passing_yards": val(
                    r,
                    "passing_yards",
                ),

                "passing_tds": val(
                    r,
                    "passing_tds",
                ),

                "interceptions": (
                    val(
                        r,
                        "passing_interceptions",
                        None,
                    )
                    if "passing_interceptions"
                    in merged.columns
                    else val(
                        r,
                        "interceptions",
                    )
                ),

                "rushing_yards": val(
                    r,
                    "rushing_yards",
                ),

                "rushing_tds": val(
                    r,
                    "rushing_tds",
                ),

                "receptions": val(
                    r,
                    "receptions",
                ),

                "receiving_yards": val(
                    r,
                    "receiving_yards",
                ),

                "receiving_tds": val(
                    r,
                    "receiving_tds",
                ),

                "fantasy_points": val(
                    r,
                    "fantasy_points",
                ),

                "fantasy_points_ppr": val(
                    r,
                    "fantasy_points_ppr",
                ),

                "source": (
                    "nflverse_weekly"
                ),
            }
        )


    if rows:

        (
            sb.table(
                "player_weekly_stats"
            )
            .upsert(
                rows,
                on_conflict=(
                    "season,week,sleeper_id"
                ),
            )
            .execute()
        )


    print(
        f"Upserted {len(rows)} rows for {season}."
    )


    return len(
        rows
    )


# ============================================================
# Main Loader
# ============================================================

def main():
    sb = service_client()

    active_season = (
        resolve_canonical_active_season(
            sb
        )
    )


    print("")
    print(
        "============================================"
    )
    print(
        "Legacy NFL Scoring Refresh"
    )
    print(
        "============================================"
    )
    print(
        f"Canonical active league season: {active_season}"
    )


    bridge_df = load_identity_bridge(
        sb
    )


    scoring_season, weekly = (
        resolve_import_season(
            active_season
        )
    )


    rows_upserted = import_weekly_frame(
        sb,
        bridge_df,
        scoring_season,
        weekly,
    )


    print("")
    print(
        "============================================"
    )
    print(
        "NFL scoring refresh complete"
    )
    print(
        "============================================"
    )

    print(
        f"League active season: {active_season}"
    )

    print(
        f"Scoring season imported: {scoring_season}"
    )

    print(
        f"Weekly rows upserted: {rows_upserted}"
    )


    if scoring_season < active_season:
        print(
            (
                "Mode: offseason fallback — "
                f"{active_season} scoring is not published yet, "
                f"so {scoring_season} remains the production authority."
            )
        )

    else:
        print(
            (
                "Mode: live season — "
                f"{active_season} scoring is available and active."
            )
        )


# ============================================================
# Entrypoint
# ============================================================

if __name__ == "__main__":
    main()