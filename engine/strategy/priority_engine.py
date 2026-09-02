from __future__ import annotations

import pandas as pd


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def build_team_priorities(scored_view: pd.DataFrame, owner_name: str, limit: int = 10) -> pd.DataFrame:
    """
    Converts scored player rows into ranked GM action priorities for one team.
    """

    if scored_view is None or scored_view.empty:
        return pd.DataFrame()

    df = scored_view.copy()

    team = df[
        df["owner"].astype(str).str.strip().str.lower()
        .eq(str(owner_name).strip().lower())
    ].copy()

    if team.empty:
        return pd.DataFrame()

    team["salary_num"] = _num(team.get("salary", 0))
    team["years_num"] = _num(team.get("years", 0))
    team["asset_score_num"] = _num(team.get("asset_score", 50), 50)
    team["franchise_value_num"] = _num(team.get("franchise_value_score", 50), 50)
    team["team_fit_num"] = _num(team.get("team_fit_score", 50), 50)

    priorities = []

    for _, r in team.iterrows():
        player = r.get("player")
        pos = r.get("pos")
        salary = float(r.get("salary_num", 0))
        years = float(r.get("years_num", 0))
        asset = float(r.get("asset_score_num", 50))
        franchise = float(r.get("franchise_value_num", 50))
        fit = float(r.get("team_fit_num", 50))
        rec = str(r.get("recommendation", ""))
        fit_label = str(r.get("team_fit_label", ""))

        # -------------------------------------------------
        # 1. Data cleanup priorities
        # -------------------------------------------------
        if pd.isna(pos) or salary <= 0 or years <= 0:
            priorities.append({
                "priority_score": 95,
                "action": "Review player data",
                "player": player,
                "pos": pos,
                "reason": "Missing position, salary, or years. This player is lowering confidence in roster analysis.",
                "category": "Data Quality",
            })
            continue

        # -------------------------------------------------
        # 2. Core protection
        # -------------------------------------------------
        if franchise >= 72 and fit >= 65:
            priorities.append({
                "priority_score": round(franchise + 8, 2),
                "action": "Protect / build around",
                "player": player,
                "pos": pos,
                "reason": f"High franchise value ({franchise:.1f}) and {fit_label.lower()} roster fit.",
                "category": "Core Asset",
            })

        # -------------------------------------------------
        # 3. Expiring decision
        # -------------------------------------------------
        if years == 1 and asset >= 58:
            priorities.append({
                "priority_score": round(asset + 10, 2),
                "action": "Decide extension or trade",
                "player": player,
                "pos": pos,
                "reason": f"Useful asset with one year left. Avoid letting value decay without a decision.",
                "category": "Contract Timing",
            })

        # -------------------------------------------------
        # 4. Sell / cut watch
        # -------------------------------------------------
        if "SELL" in rec or (salary >= 8 and franchise < 52):
            priorities.append({
                "priority_score": round(80 - franchise + salary, 2),
                "action": "Shop or cut-watch",
                "player": player,
                "pos": pos,
                "reason": f"Low franchise value ({franchise:.1f}) relative to salary (${salary:.1f}).",
                "category": "Cap Efficiency",
            })

        # -------------------------------------------------
        # 5. Surplus trade candidate
        # -------------------------------------------------
        if fit < 45 and asset >= 50:
            priorities.append({
                "priority_score": round(asset + 5, 2),
                "action": "Explore trade market",
                "player": player,
                "pos": pos,
                "reason": "Player has usable asset value but is not a strong fit for this roster.",
                "category": "Roster Balance",
            })

    if not priorities:
        return pd.DataFrame()

    out = pd.DataFrame(priorities)
    out = out.sort_values("priority_score", ascending=False).head(limit)

    return out.reset_index(drop=True)
