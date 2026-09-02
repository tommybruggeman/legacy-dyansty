from __future__ import annotations

import pandas as pd


REPLACEMENT_RANK = {
    "QB": 20,   # 10-team superflex: roughly QB20 replacement
    "RB": 30,
    "WR": 40,
    "TE": 12,
    "UNK": 60,
}


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def add_replacement_value(reasoning_view: pd.DataFrame) -> pd.DataFrame:
    """
    Adds replacement-level context.

    replacement_value_score answers:
    How much better is this player than a replacement-level player
    at the same position?
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

    replacement_scores = {}

    for pos, group in df.groupby("pos_norm"):
        rank = REPLACEMENT_RANK.get(pos, REPLACEMENT_RANK["UNK"])

        sorted_scores = (
            group["asset_score_num"]
            .sort_values(ascending=False)
            .reset_index(drop=True)
        )

        if len(sorted_scores) >= rank:
            replacement = float(sorted_scores.iloc[rank - 1])
        elif len(sorted_scores) > 0:
            replacement = float(sorted_scores.iloc[-1])
        else:
            replacement = 50.0

        replacement_scores[pos] = replacement

    df["replacement_score"] = df["pos_norm"].map(replacement_scores).fillna(50.0)

    df["value_over_replacement"] = (
        df["asset_score_num"] - df["replacement_score"]
    ).clip(lower=-25, upper=50)

    df["replacement_tier"] = pd.cut(
        df["value_over_replacement"],
        bins=[-100, 0, 5, 12, 25, 100],
        labels=[
            "Below Replacement",
            "Replacement",
            "Useful",
            "Difference Maker",
            "Elite Edge",
        ],
        include_lowest=True,
    ).astype(str)

    return df.sort_values(
        by=["value_over_replacement", "asset_score_num"],
        ascending=False,
    )
