from __future__ import annotations

import re
import unicodedata
from typing import Any

from auth import service_client


def normalize(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def num(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def clamp(x, low=0.0, high=100.0):
    return max(low, min(high, x))


def contract_pressure(pos, salary, years):
    mult = {
        "QB": 0.75,
        "RB": 1.20,
        "WR": 1.00,
        "TE": 0.95,
    }.get(pos or "UNK", 1.00)

    return round(clamp((salary * mult) + (years * 5)), 2)


def contract_flexibility(salary, years):
    return round(clamp(100 - ((salary * 1.8) + (years * 7))), 2)


def calc_upside(asset_row, kg):
    if kg:
        age = num(kg.get("age_score"))
        draft = num(kg.get("draft_capital_score"))
        college = num(kg.get("college_production_score"))
        landing = num(kg.get("landing_spot_score"))
        role = num(kg.get("role_opportunity_score"))
        market = num(kg.get("market_value_score"))

        if max(age, draft, college, landing, role, market) > 0:
            return round(clamp(
                age * 0.15
                + draft * 0.20
                + college * 0.15
                + landing * 0.20
                + role * 0.20
                + market * 0.10
            ), 2)

    engine = num(asset_row.get("engine_player_score"))
    asset = num(asset_row.get("asset_value_score"))
    return round(clamp(engine * 0.60 + asset * 0.40), 2)

def calc_risk(asset_row):
    pos = asset_row.get("pos") or "UNK"
    salary = num(asset_row.get("salary"))
    years = num(asset_row.get("years"))
    engine = num(asset_row.get("engine_player_score"))
    contract_value = num(asset_row.get("contract_value_score"))

    risk = 20
    risk += contract_pressure(pos, salary, years) * 0.35

    if contract_value <= 15:
        risk += 20

    if engine < 45:
        risk += 15

    if pos == "RB" and salary >= 12:
        risk += 10

    return round(clamp(risk), 2)


def recommend(asset_row, kg, upside, risk):
    base = asset_row.get("asset_recommendation") or "HOLD"
    asset = num(asset_row.get("asset_value_score"))
    engine = num(asset_row.get("engine_player_score"))
    pos = asset_row.get("pos") or "UNK"
    salary = num(asset_row.get("salary"))
    years = num(asset_row.get("years"))

    name = normalize(asset_row.get("player_name"))

    premium_young_wr_names = {
        "garrett wilson",
        "brandon aiyuk",
        "dk metcalf",
    }

    development_qb_names = {
        "bryce young",
    }

    if kg and kg.get("player_stage") == "rookie":
        return "ROOKIE UPSIDE HOLD"

    if engine >= 85 and pos == "QB":
        if base == "EXPENSIVE HOLD" or risk >= 50:
            return "EXPENSIVE CORE HOLD"
        return "CORE HOLD"

    if name in development_qb_names:
        if salary <= 18 and years <= 3:
            return "DEVELOPMENT QB HOLD"

    if name in premium_young_wr_names:
        if pos == "WR" and engine >= 55:
            if salary >= 30:
                return "EXPENSIVE TALENT HOLD"
            return "TALENT HOLD"

    if pos == "WR" and engine >= 60 and asset >= 45:
        if salary >= 30:
            return "EXPENSIVE TALENT HOLD"
        return "TALENT HOLD"

    if engine >= 80:
        if base == "EXPENSIVE HOLD" or risk >= 55:
            return "EXPENSIVE HOLD"
        return "CORE HOLD"

    if pos == "RB" and salary >= 20 and years >= 2:
        return "RB CONTRACT RISK"

    if upside >= 70 and risk < 65:
        return "UPSIDE HOLD"

    if risk >= 70:
        return "SHOP / PRICE CHECK"

    if asset < 35:
        return "TRADE FILLER / CUT WATCH"

    return base

def build_note(asset_row, kg, rec):
    notes = []

    if kg and kg.get("notes"):
        notes.append(kg["notes"])

    notes.append(
        f"Contract: ${num(asset_row.get('salary')):g} over {num(asset_row.get('years')):g} years."
    )

    notes.append(f"Final engine recommendation: {rec}.")

    return " ".join(notes)


def build_player_engine_snapshot():
    sb = service_client()

    asset_rows = (
        sb.table("roster_asset_values")
        .select("*")
        .execute()
        .data
        or []
    )

    kg_rows = (
        sb.table("player_knowledge_graph")
        .select("*")
        .execute()
        .data
        or []
    )

    kg_by_name = {
        normalize(r.get("player_name")): r
        for r in kg_rows
        if normalize(r.get("player_name"))
    }

    out = []

    for r in asset_rows:
        name = r.get("player_name")
        key = normalize(name)
        kg = kg_by_name.get(key, {})

        salary = num(r.get("salary"))
        years = num(r.get("years"))
        pos = r.get("pos") or kg.get("pos") or "UNK"

        upside = calc_upside(r, kg)
        risk = calc_risk({**r, "pos": pos})
        rec = recommend({**r, "pos": pos}, kg, upside, risk)

        out.append({
            "owner_team_name": r.get("owner_team_name"),
            "player_name": name,
            "normalized_name": key,
            "pos": pos,
            "nfl_team": r.get("nfl_team") or kg.get("nfl_team"),

            "salary": salary,
            "years": years,

            "engine_player_score": num(r.get("engine_player_score")),
            "contract_value_score": num(r.get("contract_value_score")),
            "asset_value_score": num(r.get("asset_value_score")),
            "base_recommendation": r.get("asset_recommendation") or "HOLD",

            "draft_class": kg.get("draft_class"),
            "draft_round": kg.get("draft_round"),
            "draft_pick": kg.get("draft_pick"),
            "college": kg.get("college"),

            "player_stage": kg.get("player_stage"),
            "archetype": kg.get("archetype"),

            "age_score": num(kg.get("age_score")),
            "draft_capital_score": num(kg.get("draft_capital_score")),
            "college_production_score": num(kg.get("college_production_score")),
            "landing_spot_score": num(kg.get("landing_spot_score")),
            "role_opportunity_score": num(kg.get("role_opportunity_score")),
            "market_value_score": num(kg.get("market_value_score")),

            "contract_pressure_score": contract_pressure(pos, salary, years),
            "contract_flexibility_score": contract_flexibility(salary, years),
            "upside_score": upside,
            "risk_score": risk,

            "engine_recommendation": rec,
            "engine_notes": build_note(r, kg, rec),
            "source": "engine_snapshot",
        })

    if out:
        sb.table("player_engine_snapshot").upsert(
            out,
            on_conflict="owner_team_name,normalized_name",
        ).execute()

    print(f"Upserted {len(out)} player_engine_snapshot rows.")
    return out


if __name__ == "__main__":
    build_player_engine_snapshot()
