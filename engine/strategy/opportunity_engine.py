from __future__ import annotations

import pandas as pd


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def build_team_opportunities(scored_view: pd.DataFrame, owner_name: str, limit: int = 12) -> pd.DataFrame:
    """
    Opportunity Engine v1.

    Converts scored player rows into GM-style opportunities:
    - What is the situation?
    - Why does it matter?
    - What action should the GM consider?
    - How urgent is it?
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
    team["contract_value_num"] = _num(team.get("contract_value_score", 50), 50)
    team["risk_num"] = _num(team.get("contract_risk_score", 0), 0)
    team["vor_num"] = _num(team.get("value_over_replacement", 0), 0)

    opportunities = []

    for _, r in team.iterrows():
        player = r.get("player")
        pos = r.get("pos")
        salary = float(r.get("salary_num", 0))
        years = float(r.get("years_num", 0))
        asset = float(r.get("asset_score_num", 50))
        franchise = float(r.get("franchise_value_num", 50))
        fit = float(r.get("team_fit_num", 50))
        contract_value = float(r.get("contract_value_num", 50))
        risk = float(r.get("risk_num", 0))
        vor = float(r.get("vor_num", 0))
        rec = str(r.get("recommendation", ""))
        fit_label = str(r.get("team_fit_label", ""))

        # -------------------------------------------------
        # Data confidence opportunity
        # -------------------------------------------------
        if pd.isna(pos) or salary <= 0 or years <= 0:
            opportunities.append({
                "opportunity_score": 98,
                "urgency": "Immediate",
                "type": "Data Quality",
                "action": "Fix player identity / contract data",
                "player": player,
                "pos": pos,
                "why": "The engine cannot confidently evaluate this player until position, salary, and years are resolved.",
                "upside": "Improves every downstream roster, priority, and trade recommendation.",
                "confidence": "High",
            })
            continue

        # -------------------------------------------------
        # Contract clock opportunity
        # -------------------------------------------------
        if years == 1 and franchise >= 58:
            opportunities.append({
                "opportunity_score": round(70 + (franchise - 50) * 0.8, 2),
                "urgency": "High",
                "type": "Contract Clock",
                "action": "Decide extend or trade",
                "player": player,
                "pos": pos,
                "why": f"{player} is useful to the franchise ({franchise:.1f}) but has only one year left.",
                "upside": "Avoids losing leverage before the contract expires.",
                "confidence": "Medium",
            })

        # -------------------------------------------------
        # Core protection opportunity
        # -------------------------------------------------
        if franchise >= 70 and fit >= 65:
            opportunities.append({
                "opportunity_score": round(franchise + 6, 2),
                "urgency": "Medium",
                "type": "Core Asset",
                "action": "Protect unless overpaid",
                "player": player,
                "pos": pos,
                "why": f"{player} combines strong franchise value ({franchise:.1f}) with {fit_label.lower()} roster fit.",
                "upside": "Maintains the strongest parts of the roster while other moves are explored.",
                "confidence": "Medium",
            })

        # -------------------------------------------------
        # Contract bargain opportunity
        # -------------------------------------------------
        if salary <= 3 and franchise >= 58 and contract_value >= 75:
            opportunities.append({
                "opportunity_score": round(franchise + contract_value * 0.15, 2),
                "urgency": "Medium",
                "type": "Contract Inefficiency",
                "action": "Hold cheap value",
                "player": player,
                "pos": pos,
                "why": f"{player} has strong roster value at only ${salary:.1f}.",
                "upside": "Cheap production creates cap flexibility elsewhere.",
                "confidence": "Medium",
            })

        # -------------------------------------------------
        # Sell / cut opportunity
        # -------------------------------------------------
        if "SELL" in rec or (salary >= 8 and franchise < 52):
            opportunities.append({
                "opportunity_score": round(75 + salary - franchise * 0.4, 2),
                "urgency": "High" if years == 1 else "Medium",
                "type": "Cap Inefficiency",
                "action": "Shop or prepare cut decision",
                "player": player,
                "pos": pos,
                "why": f"{player} carries a ${salary:.1f} salary but only {franchise:.1f} franchise value.",
                "upside": "Can free cap or convert declining value into a more useful asset.",
                "confidence": "Medium",
            })

        # -------------------------------------------------
        # Buy/hold hidden value
        # -------------------------------------------------
        if salary <= 2 and vor >= 10 and franchise >= 55:
            opportunities.append({
                "opportunity_score": round(68 + vor * 0.5, 2),
                "urgency": "Low",
                "type": "Hidden Value",
                "action": "Do not sell cheaply",
                "player": player,
                "pos": pos,
                "why": f"{player} is cheap and sits {vor:.1f} points over replacement.",
                "upside": "Potential underpriced roster edge if market perception is lower than engine value.",
                "confidence": "Low",
            })

        # -------------------------------------------------
        # Surplus trade opportunity
        # -------------------------------------------------
        if fit < 45 and asset >= 52:
            opportunities.append({
                "opportunity_score": round(60 + asset * 0.4, 2),
                "urgency": "Medium",
                "type": "Roster Surplus",
                "action": "Explore trade market",
                "player": player,
                "pos": pos,
                "why": f"{player} has asset value ({asset:.1f}) but weak team fit ({fit:.1f}).",
                "upside": "May convert surplus into a position of greater need.",
                "confidence": "Low",
            })

    if not opportunities:
        return pd.DataFrame()

    out = pd.DataFrame(opportunities)

    # Keep the strongest opportunity per player for the main GM dashboard.
    # Later, we can expose secondary opportunities as expandable details.
    out = (
        out.sort_values("opportunity_score", ascending=False)
        .drop_duplicates(subset=["player"], keep="first")
        .head(limit)
    )

    return out.reset_index(drop=True)
