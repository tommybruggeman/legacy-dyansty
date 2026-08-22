from __future__ import annotations

import pandas as pd

from auth import service_client


def _num(s, default=0):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def classify_window(score: float) -> str:
    # Calibrated for this league's compressed 10-team distribution
    if score >= 62:
        return "CONTENDER"
    if score >= 58:
        return "PLAYOFF TEAM"
    if score >= 54:
        return "FRINGE PLAYOFF"
    if score >= 48:
        return "MIDDLE"
    if score >= 38:
        return "RETOOL"
    return "REBUILD"


def classify_timeline(window_score, future_score, age_score, cap_health):
    # Timeline is about direction, not just cap pressure
    if window_score >= 62 and future_score >= 58:
        return "YOUNG CONTENDER"
    if window_score >= 62 and future_score < 52:
        return "ALL-IN"
    if window_score >= 58 and future_score >= 55:
        return "COMPETITIVE WINDOW"
    if window_score >= 56 and cap_health < 35:
        return "WIN-NOW CAP PRESSURE"
    if window_score < 54 and future_score >= 60:
        return "ASCENDING REBUILD"
    if window_score < 50 and future_score < 52:
        return "LONG REBUILD"
    if cap_health < 30:
        return "CAP CONSTRAINED"
    return "TRANSITIONING"


def _clean_json_value(v):
    import math

    if v is None:
        return None

    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return 0
        return v

    if isinstance(v, dict):
        return {k: _clean_json_value(val) for k, val in v.items()}

    if isinstance(v, list):
        return [_clean_json_value(x) for x in v]

    return v


def _clean_rows(rows):
    return [_clean_json_value(r) for r in rows]


def build_team_window_scores():
    sb = service_client()

    roster = pd.DataFrame(sb.table("roster").select("*").execute().data or [])
    values = pd.DataFrame(sb.table("player_engine_scores").select("*").execute().data or [])
    picks = pd.DataFrame(sb.table("draft_picks").select("*").execute().data or [])

    if roster.empty:
        print("No roster rows found.")
        return

    if values.empty:
        print("No player_engine_scores rows found.")
        return

    roster["sleeper_id"] = roster["sleeper_id"].astype(str)
    values["sleeper_id"] = values["sleeper_id"].astype(str)

    df = roster.merge(values, on="sleeper_id", how="left", suffixes=("", "_score"))

    for col in [
        "salary",
        "years",
        "dynasty_asset_score",
        "win_now_score",
        "contract_value_score",
        "contract_risk_score",
        "engine_score",
        "base_player_score",
        "age_curve_score",
        "final_trade_difficulty_score",
    ]:
        if col not in df.columns:
            df[col] = 0
        df[col] = _num(df[col])


    # Fallbacks because player_engine_scores does not always have the trade-layer columns yet
    if "dynasty_asset_score" not in df.columns or df["dynasty_asset_score"].fillna(0).sum() == 0:
        df["dynasty_asset_score"] = df.get("engine_score", df.get("base_player_score", 0))

    if "win_now_score" not in df.columns or df["win_now_score"].fillna(0).sum() == 0:
        df["win_now_score"] = df.get("recent_production_score", df.get("engine_score", 0))

    if "contract_value_score" not in df.columns or df["contract_value_score"].fillna(0).sum() == 0:
        df["contract_value_score"] = (100 - (df["salary"] * 2.0) - (df["years"] * 3.0)).clip(lower=0, upper=100)

    if "contract_risk_score" not in df.columns or df["contract_risk_score"].fillna(0).sum() == 0:
        df["contract_risk_score"] = ((df["salary"] * 1.5) + (df["years"] * 4.0)).clip(lower=0, upper=100)

    if "age_curve_score" not in df.columns or df["age_curve_score"].fillna(0).sum() == 0:
        df["age_curve_score"] = df.get("career_score_scaled", df.get("engine_score", 50))

    rows = []

    for owner_team_name, team_df in df.groupby("owner_team_name"):
        if not owner_team_name:
            continue

        # Skip stale/empty duplicate roster buckets
        meaningful_players = team_df[
            (team_df["sleeper_id"].notna())
            & (team_df["sleeper_id"].astype(str).str.len() > 0)
            & (team_df["dynasty_asset_score"] > 0)
        ]

        if len(meaningful_players) < 5:
            print(f"Skipping stale/empty team bucket: {owner_team_name}")
            continue

        top_win_now = team_df.sort_values("win_now_score", ascending=False).head(10)
        top_assets = team_df.sort_values("dynasty_asset_score", ascending=False).head(10)

        starters_power = float(top_win_now["win_now_score"].mean())
        future_core = float(top_assets["dynasty_asset_score"].mean())
        depth_score = float(team_df.sort_values("win_now_score", ascending=False).head(16)["win_now_score"].mean())

        age_score = float(team_df["age_curve_score"].mean())
        contract_value = float(team_df["contract_value_score"].mean())
        contract_risk = float(team_df["contract_risk_score"].mean())

        total_salary = float(team_df["salary"].sum())
        avg_salary = float(team_df["salary"].mean())
        expensive_contracts = int((team_df["salary"] >= 20).sum())
        multi_year_money = float((team_df["salary"] * team_df["years"]).sum())

        cap_health = (
            100
            - (total_salary * 0.18)
            - (avg_salary * 1.2)
            - (expensive_contracts * 4)
            - (multi_year_money * 0.035)
            + (contract_value * 0.20)
        )

        cap_health = max(0, min(100, cap_health))

        bad_contracts = team_df[
            (team_df["salary"] >= 15)
            & (team_df["contract_value_score"] < 45)
        ]

        expiring_assets = team_df[
            (team_df["years"] <= 1)
            & (team_df["dynasty_asset_score"] >= 55)
        ]

        young_core = team_df[
            team_df["dynasty_asset_score"] >= 65
        ]

        if not picks.empty and "current_owner" in picks.columns:
            team_picks = picks[picks["current_owner"] == owner_team_name]
            pick_count = len(team_picks)
            first_count = len(team_picks[team_picks["round"] == 1]) if "round" in team_picks.columns else 0
        else:
            pick_count = 0
            first_count = 0

        draft_health = min(100, 40 + pick_count * 5 + first_count * 12)

        window_score = (
            starters_power * 0.45
            + depth_score * 0.20
            + cap_health * 0.15
            + future_core * 0.10
            + draft_health * 0.10
        )

        future_score = (
            future_core * 0.45
            + age_score * 0.20
            + draft_health * 0.20
            + cap_health * 0.15
        )

        flexibility_score = (
            cap_health * 0.45
            + draft_health * 0.30
            + contract_value * 0.25
        )

        positional_summary = {}
        needs_summary = []

        for pos in ["QB", "RB", "WR", "TE"]:
            pos_df = team_df[team_df["pos"] == pos]

            pos_win_now = (
                float(pos_df.sort_values("win_now_score", ascending=False).head(3)["win_now_score"].mean())
                if not pos_df.empty else 0
            )

            pos_future = (
                float(pos_df.sort_values("dynasty_asset_score", ascending=False).head(3)["dynasty_asset_score"].mean())
                if not pos_df.empty else 0
            )

            positional_summary[pos] = {
                "count": int(len(pos_df)),
                "win_now": round(pos_win_now, 2),
                "future": round(pos_future, 2),
            }

            if pos_win_now < 40 and pos_future < 45:
                needs_summary.append(f"urgent {pos} need")
            elif pos_win_now < 50:
                needs_summary.append(f"short-term {pos} need")
            elif pos_future < 45:
                needs_summary.append(f"future {pos} need")

        trade_posture = []

        if window_score >= 70:
            trade_posture.append("should buy production")
        if future_score < 45:
            trade_posture.append("can sacrifice future value")
        if window_score < 45:
            trade_posture.append("should sell veterans")
        if draft_health >= 70:
            trade_posture.append("has draft ammo")
        if cap_health < 30:
            trade_posture.append("needs cap relief")
        elif cap_health < 40:
            trade_posture.append("limited cap flexibility")
        if len(expiring_assets) >= 3:
            trade_posture.append("has expiring asset pressure")
        if len(bad_contracts) >= 3:
            trade_posture.append("has inefficient money")

        rows.append({
            "league_id": None,
            "owner_team_name": owner_team_name,
            "window_score": round(float(window_score), 2),
            "window_label": classify_window(window_score),
            "future_score": round(float(future_score), 2),
            "depth_score": round(float(depth_score), 2),
            "cap_health_score": round(float(cap_health), 2),
            "draft_health_score": round(float(draft_health), 2),
            "flexibility_score": round(float(flexibility_score), 2),
            "timeline_label": classify_timeline(window_score, future_score, age_score, cap_health),
            "positional_summary": positional_summary,
            "needs_summary": needs_summary,
            "trade_posture": trade_posture,
            "young_core_count": int(len(young_core)),
            "expiring_asset_count": int(len(expiring_assets)),
            "bad_contract_count": int(len(bad_contracts)),
        })

    rows = _clean_rows(rows)

    print(f"Prepared team window rows: {len(rows)}")

    sb.table("team_window_scores").upsert(
        rows,
        on_conflict="owner_team_name",
    ).execute()

    print("Upsert complete.")


if __name__ == "__main__":
    build_team_window_scores()
