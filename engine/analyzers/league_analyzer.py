from __future__ import annotations

import pandas as pd

from engine.models import LeagueSummary, TeamSummary
from engine.config.league_config import DEFAULT_LEAGUE_CONFIG, LeagueConfig


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _top_n_avg(values: pd.Series, n: int) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna().sort_values(ascending=False)
    if clean.empty:
        return 0.0
    return float(clean.head(n).mean())


def _classify_window(starter_score: float, depth_score: float, cap_remaining: float) -> str:
    if starter_score >= 72 and depth_score >= 60:
        return "Contender"
    if starter_score >= 62 and cap_remaining >= 20:
        return "Hybrid"
    if starter_score < 55 and cap_remaining >= 25:
        return "Rebuild / Flexible"
    if starter_score < 55:
        return "Rebuild"
    return "Middle"


def analyze_league(reasoning_view: pd.DataFrame, config: LeagueConfig = DEFAULT_LEAGUE_CONFIG) -> LeagueSummary:
    """
    Builds one TeamSummary per team from the player-level reasoning view.
    """

    if reasoning_view is None or reasoning_view.empty:
        return LeagueSummary()

    df = reasoning_view.copy()

    if "owner" not in df.columns:
        raise ValueError("reasoning_view must include an 'owner' column")

    df["salary_num"] = _num(df.get("salary", 0))
    df["years_num"] = _num(df.get("years", 0))
    df["asset_score_num"] = _num(df.get("asset_score", 50), 50)

    teams: list[TeamSummary] = []

    for owner, team_df in df.groupby("owner", dropna=False):
        owner_name = str(owner)

        pos_counts = (
            team_df["pos"]
            .fillna("UNK")
            .astype(str)
            .str.upper()
            .value_counts()
            .to_dict()
            if "pos" in team_df.columns
            else {}
        )

        cap_used = float(team_df["salary_num"].sum())
        cap_remaining = float(config.salary_cap - cap_used)

        roster_size = int(len(team_df))
        expiring = int((team_df["years_num"] == 1).sum())
        missing_contracts = int((team_df["salary_num"] <= 0).sum())

        avg_asset_score = float(team_df["asset_score_num"].mean()) if roster_size else 0.0
        top_asset_score = float(team_df["asset_score_num"].max()) if roster_size else 0.0

        # Starter proxy:
        # Superflex-ish league, approximate top 9 players as starters.
        starter_score = _top_n_avg(team_df["asset_score_num"], 9)

        # Depth proxy:
        # Players 10-18.
        sorted_assets = team_df["asset_score_num"].sort_values(ascending=False)
        depth_slice = sorted_assets.iloc[9:18]
        depth_score = float(depth_slice.mean()) if not depth_slice.empty else 0.0

        strengths = []
        weaknesses = []

        for pos in ["QB", "RB", "WR", "TE"]:
            count = int(pos_counts.get(pos, 0))
            if pos == "QB":
                if count >= 3:
                    strengths.append("QB depth")
                elif count <= 1:
                    weaknesses.append("QB depth")
            elif pos in ["RB", "WR"]:
                if count >= 6:
                    strengths.append(f"{pos} depth")
                elif count <= 3:
                    weaknesses.append(f"{pos} depth")
            elif pos == "TE":
                if count >= 3:
                    strengths.append("TE depth")
                elif count <= 1:
                    weaknesses.append("TE depth")

        if cap_remaining >= 30:
            strengths.append("cap flexibility")
        elif cap_remaining < 5:
            weaknesses.append("cap pressure")

        if expiring >= 6:
            weaknesses.append("many expiring contracts")

        window = _classify_window(starter_score, depth_score, cap_remaining)

        summary = (
            f"{owner_name} profiles as {window}. "
            f"Starter score {starter_score:.1f}, depth score {depth_score:.1f}, "
            f"cap remaining ${cap_remaining:.1f}."
        )

        teams.append(
            TeamSummary(
                owner=owner_name,
                roster_size=roster_size,
                cap_used=cap_used,
                cap_remaining=cap_remaining,
                qb_count=int(pos_counts.get("QB", 0)),
                rb_count=int(pos_counts.get("RB", 0)),
                wr_count=int(pos_counts.get("WR", 0)),
                te_count=int(pos_counts.get("TE", 0)),
                expiring_contracts=expiring,
                missing_contracts=missing_contracts,
                avg_asset_score=avg_asset_score,
                top_asset_score=top_asset_score,
                starter_score=starter_score,
                depth_score=depth_score,
                position_counts=pos_counts,
                strengths=strengths,
                weaknesses=weaknesses,
                window=window,
                summary=summary,
            )
        )

    teams_sorted = sorted(teams, key=lambda t: t.starter_score, reverse=True)

    return LeagueSummary(
        teams=teams_sorted,
        best_team=teams_sorted[0].owner if teams_sorted else None,
        weakest_team=teams_sorted[-1].owner if teams_sorted else None,
        deepest_team=max(teams, key=lambda t: t.depth_score).owner if teams else None,
        most_cap_flexible_team=max(teams, key=lambda t: t.cap_remaining).owner if teams else None,
    )
