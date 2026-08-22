from __future__ import annotations

import pandas as pd


def _num(series, default=0.0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def build_cap_snapshot(ctx: dict) -> pd.DataFrame:
    teams = ctx.get("teams", pd.DataFrame()).copy()
    owners = ctx.get("owners", pd.DataFrame()).copy()
    contracts = ctx.get("contracts", pd.DataFrame()).copy()
    cap_adjustments = ctx.get("cap_adjustments", pd.DataFrame()).copy()
    league_rules = ctx.get("league_rules", {})

    if owners.empty:
        return pd.DataFrame()

    if ctx.get("season") is None:
        raise ValueError("Snapshot context requires an authoritative season.")
    season = int(ctx["season"])
    salary_cap = float(league_rules.get("salary_cap", 225))

    rows = []

    for _, owner in owners.iterrows():
        owner_name = str(owner.get("full_name", "")).strip()
        league_id = owner.get("league_id")
        owner_id = owner.get("id")

        team_match = pd.DataFrame()
        if not teams.empty and "team_name" in teams.columns:
            team_match = teams[
                teams["team_name"].astype(str).str.strip().eq(owner_name)
            ]

        team_id = None
        sleeper_roster_id = None
        sleeper_owner_id = None
        sleeper_team_name = None

        if not team_match.empty:
            team_row = team_match.iloc[0]
            team_id = team_row.get("id")
            sleeper_roster_id = team_row.get("sleeper_roster_id")
            sleeper_owner_id = team_row.get("sleeper_owner_id")
            sleeper_team_name = team_row.get("team_name")

        team_contracts = contracts.copy()
        if not team_contracts.empty and "owner" in team_contracts.columns:
            team_contracts = team_contracts[
                team_contracts["owner"].astype(str).str.strip().eq(owner_name)
            ]

        active_salary = 0.0
        if not team_contracts.empty and "salary" in team_contracts.columns:
            active_salary = _num(team_contracts["salary"]).sum()

        team_adjustments = cap_adjustments.copy()

        if not team_adjustments.empty and "owner_name" in team_adjustments.columns:
            team_adjustments = team_adjustments[
                team_adjustments["owner_name"].astype(str).str.strip().eq(owner_name)
            ]

        if not team_adjustments.empty and "season" in team_adjustments.columns:
            team_adjustments = team_adjustments[
                _num(team_adjustments["season"]).astype(int).eq(season)
            ]

        adjustment_total = 0.0
        if not team_adjustments.empty and "amount" in team_adjustments.columns:
            adjustment_total = _num(team_adjustments["amount"]).sum()

        cap_used = active_salary + adjustment_total
        cap_space = salary_cap - cap_used

        rows.append(
            {
                "league_id": league_id,
                "owner_id": owner_id,
                "team_id": team_id,
                "owner_name": owner_name,
                "sleeper_team_name": sleeper_team_name,
                "sleeper_roster_id": sleeper_roster_id,
                "sleeper_owner_id": sleeper_owner_id,
                "season": season,
                "salary_cap": round(salary_cap, 2),
                "active_salary": round(active_salary, 2),
                "adjustment_total": round(adjustment_total, 2),
                "cap_used": round(cap_used, 2),
                "cap_space": round(cap_space, 2),
                "is_over_cap": cap_space < 0,
            }
        )

    return pd.DataFrame(rows)
