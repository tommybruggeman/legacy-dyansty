from __future__ import annotations

import pandas as pd

from auth import service_client


SOURCE_TABLE = "roster_asset_values"
TARGET_TABLE = "player_recommendations"


def _num(v, default=0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _recommend(row):
    player = _num(row.get("engine_player_score"))
    asset = _num(row.get("asset_value_score"))
    dynasty = _num(row.get("dynasty_asset_score"))
    win_now = _num(row.get("win_now_asset_score"))
    contract = _num(row.get("contract_value_score"))
    risk = _num(row.get("dynasty_risk_score"))
    salary = _num(row.get("salary"))
    years = _num(row.get("years"))

    prospect_names = {
        "omarion hampton",
        "matthew golden",
        "jalen milroe",
        "kendre miller",
    }

    normalized_name = str(row.get("player_name") or "").strip().lower()

    rookie_or_prospect = (
        (
            row.get("rebuild_flag")
            and dynasty >= 40
            and salary <= 15
        )
        or normalized_name in prospect_names
    )

    if row.get("cornerstone_flag") and dynasty >= 70:
        return "CORE HOLD"

    if rookie_or_prospect and salary <= 5:
        return "PROSPECT HOLD"

    if rookie_or_prospect and salary <= 15:
        return "DEVELOPMENT HOLD"

    if row.get("sell_high_flag"):
        return "SELL HIGH"

    if row.get("buy_low_flag"):
        return "BUY LOW / HOLD"

    if player >= 85 and salary >= 35:
        return "EXPENSIVE CORE HOLD"

    if dynasty >= 60 and win_now < 50:
        return "DYNASTY HOLD"

    if win_now >= 60 and dynasty < 55:
        return "WIN-NOW SELL WINDOW"

    if contract <= 15 and asset < 45 and salary >= 10:
        return "SHOP CONTRACT"

    if asset < 35:
        return "CHURN / REPLACE"

    if asset < 45:
        return "SHOP"

    if asset >= 60:
        return "HOLD"

    return "DEPTH HOLD"


def _confidence(row, recommendation):
    asset = _num(row.get("asset_value_score"))
    dynasty = _num(row.get("dynasty_asset_score"))
    win_now = _num(row.get("win_now_asset_score"))
    risk = _num(row.get("dynasty_risk_score"))

    spread = abs(dynasty - win_now)

    base = 60

    if recommendation in ["CORE HOLD", "EXPENSIVE CORE HOLD"]:
        base += min(asset, dynasty) * 0.25

    elif recommendation in ["SELL HIGH", "BUY LOW / HOLD"]:
        base += spread * 0.4

    elif recommendation in ["SHOP CONTRACT", "CHURN / REPLACE", "SHOP"]:
        base += (100 - asset) * 0.25

    else:
        base += 10

    if risk >= 70:
        base -= 8

    return round(max(50, min(95, base)), 2)


def _reason(row, recommendation):
    name = (row.get("player_name") or "").strip()
    salary = _num(row.get("salary"))
    years = _num(row.get("years"))
    dynasty = _num(row.get("dynasty_asset_score"))
    win_now = _num(row.get("win_now_asset_score"))
    contract = _num(row.get("contract_value_score"))
    window = _num(row.get("dynasty_window_score"))
    stage = row.get("career_stage") or "unknown stage"

    if recommendation == "CORE HOLD":
        return f"{name} grades as a long-term cornerstone with strong dynasty value and a durable window."

    if recommendation == "PROSPECT HOLD":
        return f"{name} is a cheap future-facing asset. Do not churn before the dynasty profile has time to develop."

    if recommendation == "DEVELOPMENT HOLD":
        return f"{name} is more of a developmental dynasty hold than a current win-now asset. Be patient unless someone pays up."

    if recommendation == "EXPENSIVE CORE HOLD":
        return f"{name} is expensive at ${salary:g} over {years:g} years, but the player score is high enough to justify holding."

    if recommendation == "SELL HIGH":
        return f"{name} has useful win-now value, but the dynasty window/risk profile suggests this may be a good time to shop."

    if recommendation == "BUY LOW / HOLD":
        return f"{name} has a better dynasty profile than current production implies, making him a buy-low or patient hold."

    if recommendation == "DYNASTY HOLD":
        return f"{name} is more valuable as a future-facing dynasty asset than as a current win-now piece."

    if recommendation == "WIN-NOW SELL WINDOW":
        return f"{name} helps now, but long-term dynasty value is weaker. Contenders may value him more than rebuilders."

    if recommendation == "SHOP CONTRACT":
        return f"{name}'s contract value is weak at ${salary:g} over {years:g} years, so shop the deal if possible."

    if recommendation == "CHURN / REPLACE":
        return f"{name} has one of the lowest asset scores on the roster and is replaceable."

    if recommendation == "SHOP":
        return f"{name} is not a must-cut, but the current asset score makes him worth shopping."

    if recommendation == "HOLD":
        return f"{name} has enough combined win-now and dynasty value to hold."

    return f"{name} profiles as a depth hold with no urgent move required."


def build_player_recommendations() -> pd.DataFrame:
    sb = service_client()

    rows = sb.table(SOURCE_TABLE).select("*").execute().data or []

    if not rows:
        print("No roster asset values found.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df["recommendation"] = df.apply(_recommend, axis=1)
    df["confidence"] = df.apply(lambda r: _confidence(r, r["recommendation"]), axis=1)
    df["reasoning"] = df.apply(lambda r: _reason(r, r["recommendation"]), axis=1)

    df["priority_score"] = df.apply(
        lambda r: (
            100 - _num(r.get("asset_value_score"))
            if r["recommendation"] in ["SHOP", "SHOP CONTRACT", "CHURN / REPLACE"]
            else _num(r.get("dynasty_asset_score"))
        ),
        axis=1,
    )

    out = df[
        [
            "sleeper_id",
            "player_name",
            "pos",
            "owner_team_name",
            "salary",
            "years",
            "engine_player_score",
            "win_now_asset_score",
            "dynasty_asset_score",
            "asset_value_score",
            "dynasty_window_score",
            "market_liquidity_score",
            "dynasty_risk_score",
            "career_stage",
            "recommendation",
            "confidence",
            "priority_score",
            "reasoning",
        ]
    ].copy()

    out = out.round(2)
    out = out.replace([float("inf"), float("-inf")], None)
    out = out.astype(object).where(pd.notnull(out), None)

    records = out.to_dict("records")

    print(f"Built player recommendation rows: {len(records)}")

    if records:
        sb.table(TARGET_TABLE).upsert(
            records,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Upserted {len(records)} player recommendation rows.")

    return out


if __name__ == "__main__":
    build_player_recommendations()
