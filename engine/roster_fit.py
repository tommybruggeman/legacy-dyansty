from __future__ import annotations

# ============================================================
# Roster Fit Engine
#
# Responsible for evaluating team roster construction,
# positional depth, and positional need.
# ============================================================

import pandas as pd


# ============================================================
# Position Targets
# ============================================================

POSITION_TARGETS = {
    "QB": 3,
    "RB": 5,
    "WR": 7,
    "TE": 2,
}


POSITION_WEIGHTS = {
    "QB": 1.25,
    "RB": 1.00,
    "WR": 1.00,
    "TE": 0.85,
}


# ============================================================
# Helpers
# ============================================================

def _num(series, default=0):
    return pd.to_numeric(
        series,
        errors="coerce",
    ).fillna(default)


def _clean_pos(series):
    return (
        series
        .astype(str)
        .str.upper()
        .str.strip()
    )


# ============================================================
# Team Position Summary
# ============================================================

def team_position_summary(roster: pd.DataFrame) -> pd.DataFrame:
    if (
        roster.empty
        or "owner_name" not in roster.columns
        or "pos" not in roster.columns
    ):
        return pd.DataFrame()

    df = roster.copy()
    df["pos"] = _clean_pos(df["pos"])

    if "base_player_score" not in df.columns:
        df["base_player_score"] = 0

    if "contract_value_score" not in df.columns:
        df["contract_value_score"] = 0

    summary = (
        df.groupby(["owner_name", "pos"])
        .agg(
            players=("player", "count"),
            avg_base_value=("base_player_score", "mean"),
            avg_contract_value=("contract_value_score", "mean"),
        )
        .reset_index()
    )

    summary["target_players"] = (
        summary["pos"]
        .map(POSITION_TARGETS)
        .fillna(1)
    )

    summary["position_weight"] = (
        summary["pos"]
        .map(POSITION_WEIGHTS)
        .fillna(1)
    )

    summary["depth_gap"] = (
        summary["target_players"]
        - summary["players"]
    )

    summary["positional_need_score"] = (
        summary["depth_gap"].clip(lower=0)
        * 20
        * summary["position_weight"]
    ).clip(lower=0, upper=100).round(1)

    return summary


# ============================================================
# Attach Roster Fit Scores
# ============================================================

def attach_roster_fit_scores(roster: pd.DataFrame) -> pd.DataFrame:
    if roster.empty:
        return roster

    if "owner_name" not in roster.columns or "pos" not in roster.columns:
        out = roster.copy()
        out["roster_fit_score"] = 0
        out["positional_need_score"] = 0
        return out

    out = roster.copy()
    out["pos"] = _clean_pos(out["pos"])

    summary = team_position_summary(out)

    if summary.empty:
        out["roster_fit_score"] = 0
        out["positional_need_score"] = 0
        return out

    need_lookup = summary[
        [
            "owner_name",
            "pos",
            "positional_need_score",
        ]
    ].copy()

    out = out.merge(
        need_lookup,
        on=["owner_name", "pos"],
        how="left",
    )

    out["positional_need_score"] = _num(
        out.get("positional_need_score", 0)
    ).clip(lower=0, upper=100)

    out["roster_fit_score"] = (
        100 - out["positional_need_score"]
    ).clip(lower=0, upper=100).round(1)

    return out


# ============================================================
# Team Weaknesses
# ============================================================

def team_position_weaknesses(
    roster: pd.DataFrame,
    owner_name: str,
    limit: int = 3,
) -> pd.DataFrame:
    summary = team_position_summary(roster)

    if summary.empty:
        return summary

    team_summary = summary[
        summary["owner_name"]
        .astype(str)
        .str.lower()
        .eq(str(owner_name).lower())
    ].copy()

    if team_summary.empty:
        return pd.DataFrame()

    return (
        team_summary
        .sort_values(
            ["positional_need_score", "players"],
            ascending=[False, True],
        )
        .head(limit)
    )


# ============================================================
# Team Strengths
# ============================================================

def team_position_strengths(
    roster: pd.DataFrame,
    owner_name: str,
    limit: int = 3,
) -> pd.DataFrame:
    summary = team_position_summary(roster)

    if summary.empty:
        return summary

    team_summary = summary[
        summary["owner_name"]
        .astype(str)
        .str.lower()
        .eq(str(owner_name).lower())
    ].copy()

    if team_summary.empty:
        return pd.DataFrame()

    return (
        team_summary
        .sort_values(
            ["players", "avg_base_value"],
            ascending=[False, False],
        )
        .head(limit)
    )