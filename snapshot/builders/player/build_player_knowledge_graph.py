from __future__ import annotations

import re
import unicodedata
from typing import Any

import pandas as pd

from auth import service_client


# ============================================================
# Helpers
# ============================================================

def normalize(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def num(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def clamp(x: float, low=0.0, high=100.0) -> float:
    return max(low, min(high, x))


# ============================================================
# Temporary rookie/upside seed data
# This will become real data later.
# ============================================================

ROOKIE_CONTEXT = {
    "omarion hampton": {
        "draft_class": 2025,
        "player_stage": "rookie",
        "archetype": "premium rookie rb",
        "rookie_upside_score": 82,
        "draft_capital_score": 85,
        "college_production_score": 78,
        "landing_spot_score": 82,
        "role_opportunity_score": 76,
        "risk_note": "Rookie RB with strong draft capital and landing spot, but NFL production is not established yet.",
    },
    "matthew golden": {
        "draft_class": 2025,
        "player_stage": "rookie",
        "archetype": "rookie wr upside",
        "rookie_upside_score": 72,
        "draft_capital_score": 75,
        "college_production_score": 68,
        "landing_spot_score": 65,
        "role_opportunity_score": 62,
        "risk_note": "Rookie WR with upside, but early production and role still need to be proven.",
    },
}


# ============================================================
# Classification logic
# ============================================================

def contract_pressure(pos: str, salary: float, years: float) -> float:
    """
    Contract pressure is not bad by itself.
    It means how much the contract forces a decision.
    """
    pos = pos or "UNK"

    pos_multiplier = {
        "QB": 0.75,
        "RB": 1.20,
        "WR": 1.00,
        "TE": 0.95,
    }.get(pos, 1.00)

    return clamp((salary * pos_multiplier) + (years * 5))


def contract_flexibility(salary: float, years: float) -> float:
    """
    Higher is better.
    Cheap and short contracts are flexible.
    """
    penalty = (salary * 1.8) + (years * 7)
    return clamp(100 - penalty)


def player_stage(row: dict) -> str:
    name = normalize(row.get("player_name"))

    if name in ROOKIE_CONTEXT:
        return ROOKIE_CONTEXT[name]["player_stage"]

    years = num(row.get("years"))
    salary = num(row.get("salary"))
    score = num(row.get("engine_player_score"))

    if score >= 80:
        return "prime_core"
    if salary <= 3 and years <= 2:
        return "depth_or_stash"
    if salary >= 20 and score < 60:
        return "expensive_risk"
    return "veteran_or_standard"


def infer_archetype(row: dict) -> str:
    name = normalize(row.get("player_name"))
    pos = row.get("pos") or "UNK"
    salary = num(row.get("salary"))
    years = num(row.get("years"))
    engine = num(row.get("engine_player_score"))
    asset = num(row.get("asset_value_score"))

    if name in ROOKIE_CONTEXT:
        return ROOKIE_CONTEXT[name]["archetype"]

    if pos == "QB" and engine >= 80:
        return "elite qb"
    if pos == "QB" and salary <= 10 and asset >= 55:
        return "value qb"
    if pos == "RB" and salary >= 20:
        return "expensive rb"
    if pos == "RB" and salary <= 3:
        return "cheap rb depth"
    if pos == "WR" and salary >= 30:
        return "premium wr contract"
    if pos == "WR" and salary <= 8:
        return "value wr"
    if pos == "TE" and engine < 50:
        return "replaceable te"

    if years >= 3 and salary >= 20:
        return "long-term contract bet"

    return "standard roster asset"


def upside_score(row: dict) -> float:
    name = normalize(row.get("player_name"))

    if name in ROOKIE_CONTEXT:
        ctx = ROOKIE_CONTEXT[name]
        return round(
            (
                ctx["rookie_upside_score"] * 0.35
                + ctx["draft_capital_score"] * 0.25
                + ctx["college_production_score"] * 0.20
                + ctx["landing_spot_score"] * 0.20
            ),
            2,
        )

    engine = num(row.get("engine_player_score"))
    asset = num(row.get("asset_value_score"))
    salary = num(row.get("salary"))

    if salary <= 5 and asset >= 45:
        return clamp(asset + 10)

    return clamp((engine * 0.6) + (asset * 0.4))


def risk_score(row: dict) -> float:
    pos = row.get("pos") or "UNK"
    salary = num(row.get("salary"))
    years = num(row.get("years"))
    engine = num(row.get("engine_player_score"))
    contract_value = num(row.get("contract_value_score"))

    risk = 20

    risk += contract_pressure(pos, salary, years) * 0.35

    if contract_value <= 15:
        risk += 20

    if engine < 45:
        risk += 15

    if pos == "RB" and salary >= 12:
        risk += 10

    return round(clamp(risk), 2)


def knowledge_grade(row: dict) -> str:
    name = normalize(row.get("player_name"))
    asset = num(row.get("asset_value_score"))
    upside = upside_score(row)
    risk = risk_score(row)

    if name in ROOKIE_CONTEXT and upside >= 70:
        return "protect_rookie_upside"

    if asset >= 65:
        return "core_value"
    if upside >= 65 and risk < 60:
        return "upside_hold"
    if risk >= 70:
        return "high_risk_contract"
    if asset < 40:
        return "replacement_pressure"

    return "balanced_asset"


def recommendation_overlay(row: dict) -> str:
    grade = knowledge_grade(row)
    base = row.get("asset_recommendation") or "HOLD"

    if grade == "protect_rookie_upside":
        return "ROOKIE UPSIDE HOLD"

    if grade == "core_value" and base == "EXPENSIVE HOLD":
        return "EXPENSIVE CORE HOLD"

    if grade == "high_risk_contract":
        return "SHOP / PRICE CHECK"

    if grade == "replacement_pressure":
        return "TRADE FILLER / CUT WATCH"

    return base


def build_knowledge_row(row: dict) -> dict:
    name = normalize(row.get("player_name"))
    ctx = ROOKIE_CONTEXT.get(name, {})

    salary = num(row.get("salary"))
    years = num(row.get("years"))
    pos = row.get("pos") or "UNK"

    return {
        "player_name": row.get("player_name"),
        "owner_team_name": row.get("owner_team_name"),
        "pos": pos,

        "salary": salary,
        "years": years,
        "engine_player_score": num(row.get("engine_player_score")),
        "contract_value_score": num(row.get("contract_value_score")),
        "asset_value_score": num(row.get("asset_value_score")),
        "base_recommendation": row.get("asset_recommendation") or "HOLD",

        "player_stage": ctx.get("player_stage") or player_stage(row),
        "archetype": ctx.get("archetype") or infer_archetype(row),

        "draft_class": ctx.get("draft_class"),
        "rookie_upside_score": num(ctx.get("rookie_upside_score"), 0),
        "draft_capital_score": num(ctx.get("draft_capital_score"), 0),
        "college_production_score": num(ctx.get("college_production_score"), 0),
        "landing_spot_score": num(ctx.get("landing_spot_score"), 0),
        "role_opportunity_score": num(ctx.get("role_opportunity_score"), 0),

        "contract_pressure_score": contract_pressure(pos, salary, years),
        "contract_flexibility_score": contract_flexibility(salary, years),
        "upside_score": upside_score(row),
        "risk_score": risk_score(row),

        "knowledge_grade": knowledge_grade(row),
        "recommendation_overlay": recommendation_overlay(row),
        "risk_note": ctx.get("risk_note"),
    }


# ============================================================
# DB builder
# ============================================================

def build_player_knowledge_graph() -> pd.DataFrame:
    sb = service_client()

    rows = (
        sb.table("roster_asset_values")
        .select("*")
        .execute()
        .data
        or []
    )

    if not rows:
        print("No roster_asset_values rows found.")
        return pd.DataFrame()

    out = [build_knowledge_row(r) for r in rows]
    df = pd.DataFrame(out)

    print(f"Built player knowledge graph rows: {len(df)}")
    print(df[["player_name", "pos", "archetype", "knowledge_grade", "recommendation_overlay"]].head(20))

    return df


if __name__ == "__main__":
    df = build_player_knowledge_graph()
    print(df.head())
