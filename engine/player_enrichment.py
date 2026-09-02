from __future__ import annotations

# ============================================================
# Player Enrichment
#
# Responsible for attaching player-level data onto roster rows.
#
# Includes:
# - player metadata
# - player engine scores
# - raw dynasty rankings fallback
# - historical scoring fallback
# ============================================================

import pandas as pd

from auth import service_client


# ============================================================
# Supabase Client
# ============================================================

supabase = service_client()


# ============================================================
# Helpers
# ============================================================

def _num(series, default=0):
    return pd.to_numeric(
        series,
        errors="coerce",
    ).fillna(default)


def _clean_id(series):
    return (
        series
        .astype(str)
        .str.strip()
    )


def _clean_pos(series):
    return (
        series
        .astype(str)
        .str.upper()
        .str.strip()
    )


def _find_roster_join_key(roster: pd.DataFrame) -> str | None:
    for col in [
        "sleeper_player_id",
        "sleeper_id",
        "player_id",
    ]:
        if col in roster.columns:
            return col

    return None


def _load_table(table_name: str) -> pd.DataFrame:
    try:
        res = (
            supabase
            .table(table_name)
            .select("*")
            .execute()
        )

        return pd.DataFrame(res.data or [])

    except Exception:
        return pd.DataFrame()


# ============================================================
# Player Metadata
# ============================================================

def load_player_metadata() -> pd.DataFrame:
    return _load_table("player_metadata")


def normalize_player_metadata(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()

    if "sleeper_id" in out.columns:
        out["sleeper_id"] = _clean_id(out["sleeper_id"])

    if "player" in out.columns:
        out["player"] = (
            out["player"]
            .astype(str)
            .str.strip()
        )

    if "pos" in out.columns:
        out["pos"] = _clean_pos(out["pos"])

    if "nfl_team" in out.columns:
        out["nfl_team"] = _clean_pos(out["nfl_team"])

    for col in ["age", "years_pro"]:
        if col in out.columns:
            out[col] = pd.to_numeric(
                out[col],
                errors="coerce",
            )

    return out


def attach_player_metadata(roster: pd.DataFrame) -> pd.DataFrame:
    if roster.empty:
        return roster

    metadata = normalize_player_metadata(
        load_player_metadata()
    )

    if metadata.empty:
        return roster

    roster_key = _find_roster_join_key(roster)

    if not roster_key or "sleeper_id" not in metadata.columns:
        return roster

    out = roster.copy()

    out[roster_key] = _clean_id(out[roster_key])
    metadata["sleeper_id"] = _clean_id(metadata["sleeper_id"])

    return out.merge(
        metadata,
        left_on=roster_key,
        right_on="sleeper_id",
        how="left",
        suffixes=("", "_meta"),
    )


# ============================================================
# Player Engine Scores
# ============================================================

def load_player_engine_scores() -> pd.DataFrame:
    return _load_table("player_engine_scores")


def normalize_player_engine_scores(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()

    if "sleeper_id" in out.columns:
        out["sleeper_id"] = _clean_id(out["sleeper_id"])

    if "player_name" in out.columns:
        out["engine_player_name"] = (
            out["player_name"]
            .astype(str)
            .str.strip()
        )

    if "pos" in out.columns:
        out["engine_pos"] = _clean_pos(out["pos"])

    numeric_cols = [
        "base_player_score",
        "career_score",
        "recent_production_score",
        "trend_score",
        "durability_score",
        "rank_score",
        "age_curve_score",
        "engine_score",
        "engine_tier",
    ]

    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(
                out[col],
                errors="coerce",
            )

    keep_cols = [
        c for c in [
            "sleeper_id",
            "engine_player_name",
            "engine_pos",
            "base_player_score",
            "career_score",
            "recent_production_score",
            "trend_score",
            "durability_score",
            "rank_score",
            "age_curve_score",
            "engine_score",
            "engine_tier",
            "engine_summary",
            "source",
        ]
        if c in out.columns
    ]

    return out[keep_cols].copy()


def attach_player_engine_scores(roster: pd.DataFrame) -> pd.DataFrame:
    if roster.empty:
        return roster

    scores = normalize_player_engine_scores(
        load_player_engine_scores()
    )

    if scores.empty:
        out = roster.copy()
        out["engine_score"] = 0
        return out

    roster_key = _find_roster_join_key(roster)

    if not roster_key or "sleeper_id" not in scores.columns:
        out = roster.copy()
        out["engine_score"] = 0
        return out

    out = roster.copy()

    out[roster_key] = _clean_id(out[roster_key])
    scores["sleeper_id"] = _clean_id(scores["sleeper_id"])

    out = out.merge(
        scores,
        left_on=roster_key,
        right_on="sleeper_id",
        how="left",
        suffixes=("", "_engine"),
    )

    out["engine_score"] = _num(
        out.get("engine_score", 0)
    ).clip(lower=0, upper=100)

    return out


# ============================================================
# Player Rankings Fallback
# ============================================================

def load_player_rankings() -> pd.DataFrame:
    return _load_table("player_rankings")


def normalize_player_rankings(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()

    for col in ["sleeper_id", "player"]:
        if col in out.columns:
            out[col] = (
                out[col]
                .astype(str)
                .str.strip()
            )

    if "pos" in out.columns:
        out["pos"] = _clean_pos(out["pos"])

    if "source" in out.columns:
        out["source"] = (
            out["source"]
            .astype(str)
            .str.strip()
        )

    numeric_cols = [
        "dynasty_rank",
        "position_rank",
        "tier",
        "base_player_score",
    ]

    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(
                out[col],
                errors="coerce",
            )

    if "base_player_score" not in out.columns:
        out["base_player_score"] = 0

    out["base_player_score"] = (
        out["base_player_score"]
        .fillna(0)
        .clip(lower=0, upper=100)
    )

    keep_cols = [
        c for c in [
            "sleeper_id",
            "dynasty_rank",
            "position_rank",
            "tier",
            "base_player_score",
            "source",
        ]
        if c in out.columns
    ]

    return out[keep_cols].copy()


def attach_player_rankings(roster: pd.DataFrame) -> pd.DataFrame:
    if roster.empty:
        return roster

    rankings = normalize_player_rankings(
        load_player_rankings()
    )

    if rankings.empty:
        out = roster.copy()

        if "base_player_score" not in out.columns:
            out["base_player_score"] = 0

        return out

    roster_key = _find_roster_join_key(roster)

    if not roster_key or "sleeper_id" not in rankings.columns:
        out = roster.copy()

        if "base_player_score" not in out.columns:
            out["base_player_score"] = 0

        return out

    out = roster.copy()

    out[roster_key] = _clean_id(out[roster_key])
    rankings["sleeper_id"] = _clean_id(rankings["sleeper_id"])

    out = out.merge(
        rankings,
        left_on=roster_key,
        right_on="sleeper_id",
        how="left",
        suffixes=("", "_rank"),
    )

    if "base_player_score" not in out.columns:
        out["base_player_score"] = 0

    out["base_player_score"] = _num(
        out["base_player_score"]
    ).clip(lower=0, upper=100)

    return out


# ============================================================
# Player Scoring History Fallback
# ============================================================

def load_player_scoring_history() -> pd.DataFrame:
    return _load_table("player_scoring_history")


def normalize_scoring_history(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()

    if "sleeper_id" in out.columns:
        out["sleeper_id"] = _clean_id(out["sleeper_id"])

    if "pos" in out.columns:
        out["pos"] = _clean_pos(out["pos"])

    for col in [
        "season",
        "games_played",
        "total_points",
        "ppg",
        "positional_ppg_rank",
        "positional_total_rank",
    ]:
        if col in out.columns:
            out[col] = pd.to_numeric(
                out[col],
                errors="coerce",
            )

    return out


def build_recent_production_scores(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()

    out = out.sort_values(
        ["sleeper_id", "season"],
        ascending=[True, False],
    )

    latest = (
        out.groupby("sleeper_id")
        .head(1)
        .copy()
    )

    max_ppg = latest["ppg"].max()

    if not max_ppg:
        latest["production_score"] = 0
    else:
        latest["production_score"] = (
            latest["ppg"] / max_ppg * 100
        ).clip(lower=0, upper=100).round(1)

    keep_cols = [
        c for c in [
            "sleeper_id",
            "season",
            "games_played",
            "total_points",
            "ppg",
            "positional_ppg_rank",
            "positional_total_rank",
            "production_score",
        ]
        if c in latest.columns
    ]

    return latest[keep_cols].copy()


def attach_scoring_history(roster: pd.DataFrame) -> pd.DataFrame:
    if roster.empty:
        return roster

    history = normalize_scoring_history(
        load_player_scoring_history()
    )

    if history.empty:
        out = roster.copy()
        out["production_score"] = 0
        return out

    production = build_recent_production_scores(history)

    roster_key = _find_roster_join_key(roster)

    if not roster_key:
        out = roster.copy()
        out["production_score"] = 0
        return out

    out = roster.copy()

    out[roster_key] = _clean_id(out[roster_key])
    production["sleeper_id"] = _clean_id(production["sleeper_id"])

    out = out.merge(
        production,
        left_on=roster_key,
        right_on="sleeper_id",
        how="left",
        suffixes=("", "_production"),
    )

    if "production_score" not in out.columns:
        out["production_score"] = 0

    out["production_score"] = _num(
        out["production_score"]
    ).clip(lower=0, upper=100)

    return out


# ============================================================
# Full Roster Enrichment Pipeline
# ============================================================

def enrich_roster(roster: pd.DataFrame) -> pd.DataFrame:
    if roster.empty:
        return roster

    out = roster.copy()

    out = attach_player_metadata(out)

    out = attach_player_engine_scores(out)

    # Fallback only.
    # The main valuation now comes from player_engine_scores.
    out = attach_player_rankings(out)

    # Fallback/legacy production context.
    # The new engine already includes recent_production_score.
    out = attach_scoring_history(out)

    return out