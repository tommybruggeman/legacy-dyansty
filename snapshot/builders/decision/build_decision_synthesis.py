from __future__ import annotations

import math
import pandas as pd

from auth import service_client

TARGET_TABLE = "player_decision_synthesis"



def _normalize_player_keys(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()

    if "sleeper_id" not in df.columns:
        for alt in ["sleeper_player_id", "player_id", "sleeper_player_key"]:
            if alt in df.columns:
                df["sleeper_id"] = df[alt]
                break

    if "player_name" not in df.columns:
        for alt in ["name", "full_name"]:
            if alt in df.columns:
                df["player_name"] = df[alt]
                break

    if "owner_team_name" not in df.columns:
        for alt in ["team_name", "owner_name"]:
            if alt in df.columns:
                df["owner_team_name"] = df[alt]
                break

    if "sleeper_id" in df.columns:
        df["sleeper_id"] = df["sleeper_id"].astype(str)

    return df


def _safe_num(v, default=0.0):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except Exception:
        return default


def _decision(row):
    pos = row.get("pos")
    salary = _safe_num(row.get("salary"))
    years = _safe_num(row.get("years"))

    asset = _safe_num(row.get("dynasty_asset_score"))
    win_now = _safe_num(row.get("win_now_score"))
    contract = _safe_num(row.get("contract_value_score"))
    risk = _safe_num(row.get("contract_risk_score"))
    roi = _safe_num(row.get("contract_roi_score"))
    if roi <= 0:
        roi = _safe_num(row.get("contract_value_score"))
    situation = _safe_num(row.get("situation_score"))
    future = _safe_num(row.get("future_value_score"), asset)

    is_fa = not row.get("owner_team_name")

    if is_fa:
        if win_now >= 55 or future >= 55 or roi >= 60:
            return "TARGET FA"
        return "IGNORE FA"

    # Missing/weak score safety
    if asset <= 0 and win_now <= 0:
        return "NEEDS REVIEW"

    # Elite / cornerstone protection
    if pos == "QB" and asset >= 65:
        if salary >= 40:
            return "ELITE HOLD / EXPENSIVE ANCHOR"
        return "ELITE HOLD"

    if asset >= 70:
        if salary >= 40 and contract < 35:
            return "HOLD BUT TEST MARKET"
        return "ELITE HOLD"

    # Bad-contract logic
    if salary >= 35 and asset >= 50 and contract < 35:
        return "SHOP / RESTRUCTURE"

    if salary >= 20 and asset < 35 and win_now < 40 and contract < 45:
        return "CUT / TAKE THE HIT"

    if salary >= 25 and asset < 45 and contract < 45:
        return "SHOP OR CUT"

    # Sell-high / market logic
    if asset >= 60 and salary >= 25 and contract < 45:
        return "SELL HIGH / PRICE CHECK"

    if win_now < 45 and future < 45 and contract < 45:
        return "SHOP"

    # Positive-value logic
    if contract >= 65 and salary <= 15:
        if future >= 50 or win_now >= 45:
            return "VALUE HOLD"

    if future >= 65 and contract >= 55 and risk < 60:
        return "BUY / HOLD"

    if win_now >= 65 and situation >= 55 and contract >= 45:
        return "WIN-NOW HOLD"

    if asset >= 55 and risk < 65:
        return "HOLD"

    return "DEPTH / FLEXIBLE"


def _confidence(row, decision):
    signals = [
        _safe_num(row.get("dynasty_asset_score")),
        _safe_num(row.get("win_now_score")),
        _safe_num(row.get("contract_value_score")),
        _safe_num(row.get("contract_roi_score")),
        100 - _safe_num(row.get("contract_risk_score")),
        _safe_num(row.get("situation_score")),
    ]

    spread = max(signals) - min(signals)

    if decision in {"CUT / TAKE THE HIT", "SELL HIGH", "TARGET FA"}:
        return "HIGH"
    if spread > 45:
        return "MEDIUM"
    return "LOW"


def _reason(row, decision):
    salary = _safe_num(row.get("salary"))
    years = _safe_num(row.get("years"))
    asset = _safe_num(row.get("dynasty_asset_score"))
    win_now = _safe_num(row.get("win_now_score"))
    contract = _safe_num(row.get("contract_value_score"))
    risk = _safe_num(row.get("contract_risk_score"))
    roi = _safe_num(row.get("contract_roi_score"))
    if roi <= 0:
        roi = _safe_num(row.get("contract_value_score"))
    situation = _safe_num(row.get("situation_score"))

    return (
        f"{decision}: asset {asset:.1f}, win-now {win_now:.1f}, "
        f"contract {contract:.1f}, ROI {roi:.1f}, risk {risk:.1f}, "
        f"situation {situation:.1f}, salary ${salary:.1f}, years {years:.0f}."
    )


def build_decision_synthesis() -> pd.DataFrame:
    sb = service_client()

    roster = _normalize_player_keys(pd.DataFrame(
        sb.table("roster").select("*").execute().data or []
    ))

    values = _normalize_player_keys(pd.DataFrame(
        sb.table("player_values").select("*").execute().data or []
    ))

    contract_roi = _normalize_player_keys(pd.DataFrame(
        sb.table("player_contract_roi").select("*").execute().data or []
    ))

    situation = _normalize_player_keys(pd.DataFrame(
        sb.table("player_situation_context").select("*").execute().data or []
    ))

    recommendations = _normalize_player_keys(pd.DataFrame(
        sb.table("player_recommendations").select("*").execute().data or []
    ))

    if roster.empty and values.empty:
        print("No roster/value data found.")
        return pd.DataFrame()

    df = roster.copy()

    if "owner_team_name" in df.columns:
        df = df[df["owner_team_name"].notna()]
        df = df[df["owner_team_name"].astype(str).str.len() > 0]

    if not values.empty and "sleeper_id" in values.columns:
        merge_cols = ["sleeper_id"]
        if "player_name" in values.columns and "player_name" in df.columns:
            merge_cols.append("player_name")

        df = df.merge(
            values,
            on=merge_cols,
            how="left",
            suffixes=("", "_value"),
        )

    if not recommendations.empty and "sleeper_id" in recommendations.columns:
        keep = [
            c for c in recommendations.columns
            if c in {
                "sleeper_id",
                "owner_team_name",
                "player_name",
                "dynasty_asset_score",
                "win_now_asset_score",
                "asset_value_score",
                "dynasty_window_score",
                "market_liquidity_score",
                "dynasty_risk_score",
                "engine_player_score",
                "recommendation",
                "priority_score",
            }
        ]

        df = df.merge(
            recommendations[keep],
            on=["sleeper_id", "owner_team_name"],
            how="left",
            suffixes=("", "_rec"),
        )

        if "win_now_score" not in df.columns and "win_now_asset_score" in df.columns:
            df["win_now_score"] = df["win_now_asset_score"]

        if "future_value_score" not in df.columns and "dynasty_window_score" in df.columns:
            df["future_value_score"] = df["dynasty_window_score"]

    if not contract_roi.empty:
        keep = [
            c for c in contract_roi.columns
            if c in {
                "sleeper_id",
                "owner_team_name",
                "contract_roi_score",
                "contract_value_score",
                "contract_risk_score",
                "win_now_score",
                "future_value_score",
            }
        ]
        df = df.merge(
            contract_roi[keep],
            on=["sleeper_id", "owner_team_name"],
            how="left",
            suffixes=("", "_roi"),
        )

    if not situation.empty:
        keep = [
            c for c in situation.columns
            if c in {
                "sleeper_id",
                "owner_team_name",
                "situation_score",
                "situation_grade",
                "situation_risk_flag",
                "situation_note",
            }
        ]
        df = df.merge(
            situation[keep],
            on=["sleeper_id", "owner_team_name"],
            how="left",
            suffixes=("", "_sit"),
        )

    # Fill duplicate score columns if merge created alternatives
    for base in [
        "contract_roi_score",
        "contract_value_score",
        "contract_risk_score",
        "win_now_score",
        "future_value_score",
    ]:
        alt = f"{base}_roi"
        if base not in df.columns and alt in df.columns:
            df[base] = df[alt]
        elif base in df.columns and alt in df.columns:
            df[base] = df[base].fillna(df[alt])

    rows = []

    for _, r in df.iterrows():
        rec = r.to_dict()
        decision = _decision(rec)
        confidence = _confidence(rec, decision)

        rows.append({
            "sleeper_id": rec.get("sleeper_id"),
            "player_name": rec.get("player_name") or rec.get("player"),
            "owner_team_name": rec.get("owner_team_name"),
            "pos": rec.get("pos"),
            "nfl_team": rec.get("nfl_team"),
            "salary": _safe_num(rec.get("salary")),
            "years": _safe_num(rec.get("years")),

            "decision": decision,
            "confidence": confidence,
            "reason": _reason(rec, decision),

            "dynasty_asset_score": _safe_num(rec.get("dynasty_asset_score")),
            "win_now_score": _safe_num(rec.get("win_now_score")),
            "future_value_score": _safe_num(rec.get("future_value_score"), _safe_num(rec.get("dynasty_asset_score"))),
            "contract_value_score": _safe_num(rec.get("contract_value_score")),
            "contract_roi_score": _safe_num(rec.get("contract_roi_score")),
            "contract_risk_score": _safe_num(rec.get("contract_risk_score")),
            "situation_score": _safe_num(rec.get("situation_score")),
            "situation_grade": rec.get("situation_grade"),
            "situation_risk_flag": rec.get("situation_risk_flag"),
            "situation_note": rec.get("situation_note"),
        })

    out = pd.DataFrame(rows)

    if "owner_team_name" in out.columns:
        out = out[out["owner_team_name"].notna()]
        out = out[out["owner_team_name"].astype(str).str.len() > 0]

    if "sleeper_id" in out.columns:
        out = out[out["sleeper_id"].notna()]
        out = out[out["sleeper_id"].astype(str).str.lower() != "none"]

    # Supabase/PostgREST JSON cannot accept NaN or infinite floats.
    out = out.replace([float("inf"), float("-inf")], None)
    out = out.where(pd.notnull(out), None)
    rows = out.to_dict("records")

    print(f"Prepared decision synthesis rows: {len(out)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} player_decision_synthesis rows.")

    return out


if __name__ == "__main__":
    df = build_decision_synthesis()
    if not df.empty:
        print(df[[
            "player_name",
            "owner_team_name",
            "pos",
            "salary",
            "years",
            "decision",
            "confidence",
            "reason",
        ]].head(30).to_string(index=False))
