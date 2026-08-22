from __future__ import annotations

import pandas as pd


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _safe_cols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    keep = [c for c in cols if c in df.columns]
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


# ------------------------------------------------------------
# Base Scoring Engine
# ------------------------------------------------------------

def roster_with_scores(ctx: dict) -> pd.DataFrame:
    roster = ctx.get("my_roster", pd.DataFrame()).copy()

    if roster.empty:
        return roster

    roster["salary_num"] = _num(roster.get("salary", 0))
    roster["years_num"] = _num(roster.get("years", 0))

    if "is_rookie" in roster.columns:
        roster["is_rookie_bool"] = roster["is_rookie"].fillna(False).astype(bool)
    else:
        roster["is_rookie_bool"] = False

    pos_avg = (
        roster.groupby("pos")["salary_num"]
        .transform("mean")
        .replace(0, 1)
    )

    roster["pos_salary_ratio"] = (
        roster["salary_num"] / pos_avg
    ).round(2)

    roster["contract_weight"] = (
        roster["salary_num"] * roster["years_num"]
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

    roster["contract_grade"] = roster["contract_value_score"].apply(_grade_from_score)

    return roster


# ------------------------------------------------------------
# Contract Analysis
# ------------------------------------------------------------

def worst_contracts(
    ctx: dict,
    limit: int = 5,
) -> pd.DataFrame:
    df = roster_with_scores(ctx)

    if df.empty:
        return df

    out = (
        df.sort_values(
            ["contract_risk_score", "salary_num", "years_num"],
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
            "pos_salary_ratio",
            "contract_risk_score",
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
            ["contract_value_score", "salary_num"],
            ascending=[False, True],
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
        ["pos", "contract_value_score"],
        ascending=[True, False],
    )

    return _safe_cols(
        out,
        [
            "player",
            "pos",
            "salary",
            "years",
            "is_rookie",
            "pos_salary_ratio",
            "contract_value_score",
            "contract_risk_score",
            "contract_grade",
        ],
    )


# ------------------------------------------------------------
# Roster Analysis
# ------------------------------------------------------------

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
            avg_contract_value=("contract_value_score", "mean"),
            avg_contract_risk=("contract_risk_score", "mean"),
        )
        .reset_index()
    )

    summary["avg_salary"] = summary["avg_salary"].round(1)
    summary["avg_years"] = summary["avg_years"].round(1)
    summary["avg_contract_value"] = summary["avg_contract_value"].round(1)
    summary["avg_contract_risk"] = summary["avg_contract_risk"].round(1)

    return summary.sort_values("avg_contract_value", ascending=False)


# ------------------------------------------------------------
# Team Strength / Weakness Analysis
# ------------------------------------------------------------

def team_strengths(
    ctx: dict,
    limit: int = 3,
) -> pd.DataFrame:
    summary = position_summary(ctx)

    if summary.empty:
        return summary

    return summary.sort_values(
        ["avg_contract_value", "players"],
        ascending=[False, False],
    ).head(limit)


def team_weaknesses(
    ctx: dict,
    limit: int = 3,
) -> pd.DataFrame:
    summary = position_summary(ctx)

    if summary.empty:
        return summary

    return summary.sort_values(
        ["avg_contract_value", "players"],
        ascending=[True, True],
    ).head(limit)


# ------------------------------------------------------------
# Trade Analysis
# ------------------------------------------------------------

def trade_candidates(
    ctx: dict,
    limit: int = 5,
) -> pd.DataFrame:
    df = roster_with_scores(ctx)

    if df.empty:
        return df

    df["trade_candidate_score"] = (
        df["salary_num"] * 1.2
        + df["contract_risk_score"] * 0.8
        - df["years_num"] * 1.5
        - df["is_rookie_bool"].astype(int) * 6.0
    ).round(1)

    out = (
        df.sort_values(
            ["trade_candidate_score", "salary_num"],
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
            "contract_risk_score",
            "trade_candidate_score",
            "contract_grade",
        ],
    )


# ------------------------------------------------------------
# Draft Analysis
# ------------------------------------------------------------

def draft_targets(ctx: dict) -> pd.DataFrame:
    return pd.DataFrame()


# ------------------------------------------------------------
# Start / Sit Analysis
# ------------------------------------------------------------

def start_sit(ctx: dict) -> pd.DataFrame:
    return pd.DataFrame()