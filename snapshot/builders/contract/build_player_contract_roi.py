from __future__ import annotations

import math
import pandas as pd

from auth import service_client

TARGET_TABLE = "player_contract_roi"


def _safe(v, default=0.0):
    try:
        if v is None or pd.isna(v) or math.isinf(float(v)):
            return default
        return float(v)
    except Exception:
        return default


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _expected_salary(asset, pos):
    if pos == "QB":
        return 4 + asset * 0.55
    if pos == "WR":
        return 2 + asset * 0.48
    if pos == "RB":
        return 1 + asset * 0.42
    if pos == "TE":
        return 1 + asset * 0.35
    return 1 + asset * 0.35


def _roi_score(row):
    asset = _safe(row.get("dynasty_asset_score"))
    win_now = _safe(row.get("win_now_asset_score"))
    salary = _safe(row.get("salary"))
    years = _safe(row.get("years"))
    pos = row.get("pos")

    expected = _expected_salary(asset, pos)
    surplus = expected - salary

    asset_component = asset * 0.35
    win_now_component = win_now * 0.20
    surplus_component = 50 + surplus * 2.25

    years_penalty = 0
    if salary >= 25 and years >= 3:
        years_penalty = 8
    if salary >= 35 and years >= 3:
        years_penalty = 14
    if salary >= 45 and years >= 3:
        years_penalty = 20

    score = asset_component + win_now_component + surplus_component * 0.45 - years_penalty
    return _clamp(score)


def _risk_score(row):
    salary = _safe(row.get("salary"))
    years = _safe(row.get("years"))
    asset = _safe(row.get("dynasty_asset_score"))
    win_now = _safe(row.get("win_now_asset_score"))

    risk = 0

    if salary >= 20:
        risk += 15
    if salary >= 30:
        risk += 15
    if salary >= 40:
        risk += 20
    if years >= 3:
        risk += 12
    if asset < 45:
        risk += 15
    if win_now < 40:
        risk += 10

    return _clamp(risk)


def _contract_label(roi, risk):
    if roi >= 70 and risk <= 35:
        return "STRONG VALUE"
    if roi >= 55:
        return "FAIR VALUE"
    if roi >= 40:
        return "THIN VALUE"
    if risk >= 70:
        return "BAD CONTRACT"
    return "OVERPAID"


def build_player_contract_roi() -> pd.DataFrame:
    sb = service_client()

    roster = pd.DataFrame(sb.table("roster").select("*").execute().data or [])
    recs = pd.DataFrame(sb.table("player_recommendations").select("*").execute().data or [])

    if roster.empty:
        print("No roster rows found.")
        return pd.DataFrame()

    if "player" in roster.columns and "player_name" not in roster.columns:
        roster["player_name"] = roster["player"]

    df = roster.copy()

    if not recs.empty:
        keep = [
            c for c in recs.columns
            if c in {
                "sleeper_id",
                "owner_team_name",
                "dynasty_asset_score",
                "win_now_asset_score",
                "asset_value_score",
                "dynasty_risk_score",
                "market_liquidity_score",
                "engine_player_score",
            }
        ]

        df = df.merge(
            recs[keep],
            on=["sleeper_id", "owner_team_name"],
            how="left",
        )

    rows = []

    for _, r in df.iterrows():
        rec = r.to_dict()

        roi = _roi_score(rec)
        risk = _risk_score(rec)
        label = _contract_label(roi, risk)

        rows.append({
            "sleeper_id": rec.get("sleeper_id"),
            "owner_team_name": rec.get("owner_team_name"),
            "player_name": rec.get("player_name") or rec.get("player"),
            "pos": rec.get("pos"),
            "salary": _safe(rec.get("salary")),
            "years": _safe(rec.get("years")),
            "dynasty_asset_score": _safe(rec.get("dynasty_asset_score")),
            "win_now_score": _safe(rec.get("win_now_asset_score")),
            "contract_roi_score": roi,
            "contract_risk_score": risk,
            "contract_label": label,
            "contract_note": (
                f"{label}: ROI {roi:.1f}, risk {risk:.1f}, "
                f"salary ${_safe(rec.get('salary')):.1f}, years {_safe(rec.get('years')):.0f}."
            ),
        })

    out = pd.DataFrame(rows)
    out = out.where(pd.notnull(out), None)
    rows = out.to_dict("records")

    print(f"Prepared contract ROI rows: {len(rows)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} player_contract_roi rows.")
    return out


if __name__ == "__main__":
    df = build_player_contract_roi()
    print(df.sort_values("contract_roi_score").head(30).to_string(index=False))
