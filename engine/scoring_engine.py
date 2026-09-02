from __future__ import annotations

# ============================================================
# Scoring Engine
#
# Responsible for converting enriched roster data into
# player valuation scores.
#
# Inputs expected from engine_context:
# - contracts
# - player metadata
# - player rankings
# - roster fit scores
# ============================================================

import pandas as pd


# ============================================================
# Helpers
# ============================================================

def _num(series, default=0):
    return pd.to_numeric(
        series,
        errors="coerce",
    ).fillna(default)


def _safe_cols(
    df: pd.DataFrame,
    cols: list[str],
) -> pd.DataFrame:
    keep = [
        c for c in cols
        if c in df.columns
    ]

    return df[keep].copy()


def _grade_from_score(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"

    return "F"


def _clean_pos(series):
    return (
        series
        .astype(str)
        .str.upper()
        .str.strip()
    )


# ============================================================
# Base Scoring Engine
# ============================================================

def roster_with_scores(ctx: dict) -> pd.DataFrame:
    roster = ctx.get("rosters", [])

    if isinstance(roster, list):
        roster = pd.DataFrame(roster)

    roster = roster.copy()

    if roster.empty:
        return roster

    # --------------------------------------------------------
    # Base Numeric Fields
    # --------------------------------------------------------

    roster["salary_num"] = _num(
        roster.get("salary", 0)
    )

    roster["years_num"] = _num(
        roster.get("years", 0)
    )

    if "pos" in roster.columns:
        roster["pos"] = _clean_pos(roster["pos"])
    else:
        roster["pos"] = "UNK"

    # --------------------------------------------------------
    # Rookie Flag
    # --------------------------------------------------------

    if "is_rookie" in roster.columns:
        roster["is_rookie_bool"] = (
            roster["is_rookie"]
            .fillna(False)
            .astype(bool)
        )
    else:
        roster["is_rookie_bool"] = False

    # --------------------------------------------------------
    # Ranking / Football Value
    # --------------------------------------------------------

    if "engine_score" in roster.columns:
        roster["base_value_score"] = _num(
            roster.get("engine_score", 0)
        ).clip(lower=0, upper=100)

    elif "base_player_score" in roster.columns:
        roster["base_value_score"] = _num(
            roster.get("base_player_score", 0)
        ).clip(lower=0, upper=100)

    else:
        roster["base_value_score"] = 0

    # --------------------------------------------------------
    # Historical Production Value
    # --------------------------------------------------------

    if "recent_production_score" in roster.columns:
        roster["production_score"] = _num(
            roster.get("recent_production_score", 0)
        ).clip(lower=0, upper=100)

    elif "production_score" in roster.columns:
        roster["production_score"] = _num(
            roster.get("production_score", 0)
        ).clip(lower=0, upper=100)

    else:
        roster["production_score"] = 0

    # --------------------------------------------------------
    # Roster Fit Defaults
    # --------------------------------------------------------

    if "roster_fit_score" not in roster.columns:
        roster["roster_fit_score"] = 0

    if "positional_need_score" not in roster.columns:
        roster["positional_need_score"] = 0

    roster["roster_fit_score"] = _num(
        roster.get("roster_fit_score", 0)
    ).clip(lower=0, upper=100)

    roster["positional_need_score"] = _num(
        roster.get("positional_need_score", 0)
    ).clip(lower=0, upper=100)

    # --------------------------------------------------------
    # Contract Risk / Value
    # --------------------------------------------------------

    pos_avg = (
        roster.groupby("pos")["salary_num"]
        .transform("mean")
        .replace(0, 1)
    )

    roster["pos_salary_ratio"] = (
        roster["salary_num"] / pos_avg
    ).round(2)

    roster["contract_weight"] = (
        roster["salary_num"]
        * roster["years_num"]
    )

    roster["contract_risk_score"] = (
        roster["salary_num"] * 1.2
        + roster["years_num"] * 3.0
        + roster["pos_salary_ratio"] * 5.0
        - roster["is_rookie_bool"].astype(int) * 4.0
    ).round(1)

    roster["contract_value_score"] = (
        100 - roster["contract_risk_score"]
    ).clip(lower=0, upper=100).round(1)

    roster["contract_grade"] = (
        roster["contract_value_score"]
        .apply(_grade_from_score)
    )

    # --------------------------------------------------------
    # Trade Value
    #
    # This is the first blended valuation score.
    #
    # Current weights:
    # - 60% player quality / dynasty value
    # - 25% contract value
    # - 15% roster fit
    # --------------------------------------------------------

    roster["trade_value_score"] = (
        roster["base_value_score"] * 0.45
        + roster["production_score"] * 0.25
        + roster["contract_value_score"] * 0.20
        + roster["roster_fit_score"] * 0.10
    ).round(1)

    roster["trade_grade"] = (
        roster["trade_value_score"]
        .apply(_grade_from_score)
    )

    return roster


# ============================================================
# Contract Analysis
# ============================================================

def worst_contracts(
    ctx: dict,
    limit: int = 5,
) -> pd.DataFrame:
    df = roster_with_scores(ctx)

    if df.empty:
        return df

    out = (
        df.sort_values(
            [
                "contract_risk_score",
                "salary_num",
                "years_num",
            ],
            ascending=False,
        )
        .head(limit)
    )

    return _safe_cols(
        out,
        [
            "player",
            "pos",
            "salary",
            "years",
            "base_value_score",
            "production_score",
            "pos_salary_ratio",
            "contract_risk_score",
            "contract_value_score",
            "contract_grade",
        ],
    )


def best_contracts(
    ctx: dict,
    limit: int = 5,
) -> pd.DataFrame:
    df = roster_with_scores(ctx)

    if df.empty:
        return df

    out = (
        df.sort_values(
            [
                "contract_value_score",
                "base_value_score",
                "salary_num",
            ],
            ascending=[False, False, True],
        )
        .head(limit)
    )

    return _safe_cols(
        out,
        [
            "player",
            "pos",
            "salary",
            "years",
            "base_value_score",
            "production_score",
            "pos_salary_ratio",
            "contract_value_score",
            "contract_grade",
        ],
    )


def contract_snapshot(ctx: dict) -> pd.DataFrame:
    df = roster_with_scores(ctx)

    if df.empty:
        return df

    out = df.sort_values(
        [
            "pos",
            "contract_value_score",
            "base_value_score",
        ],
        ascending=[True, False, False],
    )

    return _safe_cols(
        out,
        [
            "player",
            "pos",
            "salary",
            "years",
            "is_rookie",
            "base_value_score",
            "pos_salary_ratio",
            "contract_value_score",
            "contract_risk_score",
            "contract_grade",
        ],
    )


# ============================================================
# Roster Analysis
# ============================================================

def position_summary(ctx: dict) -> pd.DataFrame:
    roster = roster_with_scores(ctx)

    if roster.empty:
        return pd.DataFrame()

    summary = (
        roster.groupby("pos")
        .agg(
            players=("player", "count"),
            total_salary=("salary_num", "sum"),
            avg_salary=("salary_num", "mean"),
            avg_years=("years_num", "mean"),
            avg_base_value=("base_value_score", "mean"),
            avg_contract_value=("contract_value_score", "mean"),
            avg_contract_risk=("contract_risk_score", "mean"),
            avg_trade_value=("trade_value_score", "mean"),
            avg_roster_fit=("roster_fit_score", "mean"),
            avg_positional_need=("positional_need_score", "mean"),
            avg_production=("production_score", "mean"),
        )
        .reset_index()
    )

    round_cols = [
        "avg_salary",
        "avg_years",
        "avg_base_value",
        "avg_contract_value",
        "avg_contract_risk",
        "avg_trade_value",
        "avg_roster_fit",
        "avg_positional_need",
    ]

    for col in round_cols:
        if col in summary.columns:
            summary[col] = summary[col].round(1)

    return summary.sort_values(
        "avg_trade_value",
        ascending=False,
    )


# ============================================================
# Team Strength / Weakness Analysis
# ============================================================

def team_strengths(
    ctx: dict,
    limit: int = 3,
) -> pd.DataFrame:
    summary = position_summary(ctx)

    if summary.empty:
        return summary

    return (
        summary
        .sort_values(
            [
                "avg_trade_value",
                "players",
            ],
            ascending=[False, False],
        )
        .head(limit)
    )


def team_weaknesses(
    ctx: dict,
    limit: int = 3,
) -> pd.DataFrame:
    summary = position_summary(ctx)

    if summary.empty:
        return summary

    return (
        summary
        .sort_values(
            [
                "avg_positional_need",
                "avg_trade_value",
            ],
            ascending=[False, True],
        )
        .head(limit)
    )


# ============================================================
# Trade Analysis
# ============================================================

def trade_candidates(
    ctx: dict,
    limit: int = 5,
) -> pd.DataFrame:
    df = roster_with_scores(ctx)

    if df.empty:
        return df

    df["trade_candidate_score"] = (
        df["salary_num"] * 0.75
        + df["contract_risk_score"] * 0.65
        - df["base_value_score"] * 0.35
        - df["years_num"] * 1.00
        - df["is_rookie_bool"].astype(int) * 6.0
    ).round(1)

    out = (
        df.sort_values(
            [
                "trade_candidate_score",
                "contract_risk_score",
                "salary_num",
            ],
            ascending=False,
        )
        .head(limit)
    )

    return _safe_cols(
        out,
        [
            "player",
            "pos",
            "salary",
            "years",
            "base_value_score",
            "contract_risk_score",
            "contract_value_score",
            "trade_candidate_score",
            "contract_grade",
        ],
    )


def player_values_snapshot(
    ctx: dict,
    limit: int = 25,
) -> pd.DataFrame:
    df = roster_with_scores(ctx)

    if df.empty:
        return df

    out = (
        df.sort_values(
            "trade_value_score",
            ascending=False,
        )
        .head(limit)
    )

    return _safe_cols(
        out,
        [
            "player",
            "pos",
            "salary",
            "years",
            "base_value_score",
            "production_score",
            "contract_value_score",
            "roster_fit_score",
            "trade_value_score",
            "trade_grade",
        ],
    )


# ============================================================
# Draft Analysis
# ============================================================

def draft_targets(ctx: dict) -> pd.DataFrame:
    return pd.DataFrame()


# ============================================================
# Start / Sit Analysis
# ============================================================

def start_sit(ctx: dict) -> pd.DataFrame:
    return pd.DataFrame()