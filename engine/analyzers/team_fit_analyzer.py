from __future__ import annotations

import pandas as pd


IDEAL_POSITION_COUNTS = {
    "QB": 3,
    "RB": 6,
    "WR": 7,
    "TE": 3,
}


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def add_team_fit(reasoning_view: pd.DataFrame) -> pd.DataFrame:
    """
    Adds team-specific fit context.

    team_fit_score answers:
    How important is this player to this specific roster construction?
    """

    if reasoning_view is None or reasoning_view.empty:
        return pd.DataFrame()

    df = reasoning_view.copy()

    df["pos_norm"] = (
        df.get("pos", "UNK")
        .fillna("UNK")
        .astype(str)
        .str.upper()
    )

    df["asset_score_num"] = _num(df.get("asset_score", 50), 50)

    fit_scores = []
    fit_labels = []
    fit_reasons = []

    for idx, row in df.iterrows():
        owner = row.get("owner")
        pos = row.get("pos_norm", "UNK")

        team = df[df["owner"].astype(str).eq(str(owner))]
        same_pos = team[team["pos_norm"].eq(pos)].copy()

        pos_count = len(same_pos)

        # Unknown-position players should not drive roster strategy.
        if pos == "UNK":
            fit_scores.append(25)
            fit_labels.append("Unknown / Review")
            fit_reasons.append(
                f"{row.get('player')} is missing position data, so team fit cannot be trusted yet."
            )
            continue

        ideal_count = IDEAL_POSITION_COUNTS.get(pos, 4)

        player_asset = float(row.get("asset_score_num", 50))

        same_pos_assets = (
            same_pos["asset_score_num"]
            .sort_values(ascending=False)
            .reset_index(drop=True)
        )

        pos_rank = None
        for rank, value in enumerate(same_pos_assets, start=1):
            if abs(float(value) - player_asset) < 0.0001:
                pos_rank = rank
                break

        if pos_rank is None:
            pos_rank = pos_count

        scarcity_bonus = 0

        if pos_count < ideal_count:
            scarcity_bonus = 12
        elif pos_count == ideal_count:
            scarcity_bonus = 4
        elif pos_count > ideal_count + 2:
            scarcity_bonus = -8
        elif pos_count > ideal_count:
            scarcity_bonus = -3

        rank_bonus = 0

        if pos_rank == 1:
            rank_bonus = 10
        elif pos_rank == 2:
            rank_bonus = 5
        elif pos_rank <= 4:
            rank_bonus = 1
        else:
            rank_bonus = -5

        team_fit_score = max(0, min(100, 50 + scarcity_bonus + rank_bonus))

        if team_fit_score >= 70:
            label = "Critical Fit"
        elif team_fit_score >= 58:
            label = "Strong Fit"
        elif team_fit_score >= 45:
            label = "Neutral Fit"
        else:
            label = "Surplus / Tradeable"

        reason = (
            f"{owner} has {pos_count} {pos}s. "
            f"{row.get('player')} ranks {pos_rank} at the position on this roster."
        )

        fit_scores.append(team_fit_score)
        fit_labels.append(label)
        fit_reasons.append(reason)

    df["team_fit_score"] = fit_scores
    df["team_fit_label"] = fit_labels
    df["team_fit_reason"] = fit_reasons

    df["franchise_value_score"] = (
        df["asset_score_num"] * 0.70
        + df["team_fit_score"] * 0.30
    ).clip(lower=0, upper=100)

    return df.sort_values(
        by=["franchise_value_score", "asset_score_num"],
        ascending=False,
    )
