from __future__ import annotations

import pandas as pd


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def infer_team_needs(roster: pd.DataFrame) -> dict[str, float]:
    if roster is None or roster.empty:
        return {"QB": 5, "RB": 5, "WR": 5, "TE": 5}

    pos_col = None
    for c in ["pos", "position", "player_position"]:
        if c in roster.columns:
            pos_col = c
            break

    if pos_col is None:
        return {"QB": 5, "RB": 5, "WR": 5, "TE": 5}

    pos_counts = roster[pos_col].value_counts().to_dict()

    needs = {
        "QB": 0,
        "RB": 0,
        "WR": 0,
        "TE": 0,
    }

    if pos_counts.get("QB", 0) < 3:
        needs["QB"] += 20

    if pos_counts.get("RB", 0) < 5:
        needs["RB"] += 20

    if pos_counts.get("WR", 0) < 7:
        needs["WR"] += 20

    if pos_counts.get("TE", 0) < 2:
        needs["TE"] += 15

    return needs


def recommend_rookie_targets(
    prospects: pd.DataFrame,
    roster: pd.DataFrame,
    pick_number: int = 2,
    draft_year: int | None = None,
    unavailable_players: list[str] | None = None,
) -> pd.DataFrame:
    if prospects is None or prospects.empty:
        return pd.DataFrame()

    df = prospects.copy()

    if draft_year is not None and "draft_year" in df.columns:
        df = df[df["draft_year"].astype(str) == str(draft_year)].copy()

    if df.empty:
        return df

    # Exclude players already on the user's roster.
    if roster is not None and not roster.empty:
        roster_name_col = None
        for c in ["player", "player_name", "name"]:
            if c in roster.columns:
                roster_name_col = c
                break

        if roster_name_col is not None and "player_name" in df.columns:
            owned = set(
                roster[roster_name_col]
                .astype(str)
                .str.strip()
                .str.lower()
                .tolist()
            )

            df = df[
                ~df["player_name"]
                .astype(str)
                .str.strip()
                .str.lower()
                .isin(owned)
            ].copy()

    if unavailable_players:
        unavailable = {
            str(name).strip().lower()
            for name in unavailable_players
            if str(name).strip()
        }

        df = df[
            ~df["player_name"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin(unavailable)
        ].copy()

    if df.empty:
        return df

    team_needs = infer_team_needs(roster)

    df["prospect_score_num"] = _num(df.get("prospect_score", 0))
    df["draft_capital_score_num"] = _num(df.get("draft_capital_score", 0))
    df["landing_spot_score_num"] = _num(df.get("landing_spot_score", 0))
    df["opportunity_score_num"] = _num(df.get("opportunity_score", 0))
    df["scheme_fit_score_num"] = _num(df.get("scheme_fit_score", 0))

    df["need_score"] = df["position"].map(team_needs).fillna(0)

    # Availability estimate:
    # At 1.02, top prospects are still relevant.
    # Later picks should discount players likely gone.
    df["availability_score"] = 100
    if pick_number > 1:
        df["availability_score"] = (
            100 - ((df["prospect_score_num"].rank(ascending=False, method="first") - pick_number).abs() * 8)
        ).clip(lower=35, upper=100)

    df["recommendation_score"] = (
        df["prospect_score_num"] * 0.35
        + df["draft_capital_score_num"] * 0.18
        + df["landing_spot_score_num"] * 0.14
        + df["opportunity_score_num"] * 0.14
        + df["scheme_fit_score_num"] * 0.09
        + df["need_score"] * 0.05
        + df["availability_score"] * 0.05
    ).round(2)

    return df.sort_values("recommendation_score", ascending=False)


def explain_rookie_recommendation(row: pd.Series, pick_number: int = 2) -> str:
    name = row.get("player_name", "This prospect")
    pos = row.get("position", "")
    team = row.get("nfl_team", "")
    prospect_score = row.get("prospect_score", row.get("prospect_score_num", 0))
    draft_capital = row.get("draft_capital_score", row.get("draft_capital_score_num", 0))
    landing = row.get("landing_spot_score", row.get("landing_spot_score_num", 0))
    opportunity = row.get("opportunity_score", row.get("opportunity_score_num", 0))
    role = row.get("fantasy_role", "")
    upside = row.get("upside_notes", "")
    risk = row.get("risk_notes", "")

    return (
        f"At pick 1.{pick_number:02d}, {name} is a strong target because he carries a "
        f"{prospect_score} prospect score, {draft_capital} draft capital score, "
        f"{landing} landing spot score, and {opportunity} opportunity score. "
        f"He profiles as: {role}. "
        f"Upside: {upside} "
        f"Risk: {risk}"
    )
