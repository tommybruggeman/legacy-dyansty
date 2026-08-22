from __future__ import annotations

import pandas as pd

from auth import service_client


TARGET_TABLE = "team_future_context"


def _safe_num(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _grade(score: float) -> str:
    if score >= 85:
        return "ELITE"
    if score >= 70:
        return "STRONG"
    if score >= 55:
        return "STABLE"
    if score >= 40:
        return "FRAGILE"
    return "CRITICAL"


def _window(future_score: float, win_now: float) -> str:
    if future_score >= 70 and win_now >= 60:
        return "CONTENDER WITH FUTURE"
    if future_score >= 70:
        return "ASCENDING"
    if win_now >= 65 and future_score < 50:
        return "WIN-NOW PRESSURE"
    if future_score < 40:
        return "REBUILD NEEDED"
    return "MIDDLE BUILD"


def build_team_future_context() -> pd.DataFrame:
    sb = service_client()

    teams = pd.DataFrame(sb.table("teams").select("*").execute().data or [])
    roster = pd.DataFrame(sb.table("rosters_current").select("*").execute().data or [])
    picks = pd.DataFrame(sb.table("draft_picks").select("*").execute().data or [])
    scores = pd.DataFrame(sb.table("player_engine_scores").select("*").execute().data or [])

    if roster.empty:
        print("No rosters_current rows found.")
        return pd.DataFrame()

    app_league_id = None
    if not teams.empty and "league_id" in teams.columns:
        app_league_id = teams["league_id"].dropna().iloc[0]

    if app_league_id is None and not picks.empty and "league_id" in picks.columns:
        app_league_id = picks["league_id"].dropna().iloc[0]

    roster["owner_team_name"] = roster["team_id"].astype(str).str.strip()
    roster["player_id"] = roster["player_id"].astype(str)

    if not scores.empty:
        scores["sleeper_id"] = scores["sleeper_id"].astype(str)
        merged_all = roster.merge(
            scores,
            left_on="player_id",
            right_on="sleeper_id",
            how="left",
        )
    else:
        merged_all = roster.copy()

    owner_names = sorted(roster["owner_team_name"].dropna().unique().tolist())

    rows = []

    for owner_team_name in owner_names:
        team_roster = roster[roster["owner_team_name"] == owner_team_name].copy()
        merged = merged_all[merged_all["owner_team_name"] == owner_team_name].copy()

        team_picks = pd.DataFrame()
        if not picks.empty and "current_owner" in picks.columns:
            team_picks = picks[
                picks["current_owner"].astype(str).str.strip() == owner_team_name
            ].copy()

        roster_count = len(team_roster)

        engine_score = _safe_num(merged["engine_score"].mean(), 50) if "engine_score" in merged else 50
        rank_score = _safe_num(merged["rank_score"].mean(), 50) if "rank_score" in merged else 50
        career_score = _safe_num(merged["career_score"].mean(), 50) if "career_score" in merged else 50
        recent_score = _safe_num(merged["recent_production_score"].mean(), 50) if "recent_production_score" in merged else 50
        durability_score = _safe_num(merged["durability_score"].mean(), 50) if "durability_score" in merged else 50
        age_curve_score = _safe_num(merged["age_curve_score"].mean(), 50) if "age_curve_score" in merged else 50

        young_core_count = 0
        if "engine_score" in merged.columns:
            young_core_count = int(
                pd.to_numeric(merged["engine_score"], errors="coerce")
                .fillna(0)
                .ge(55)
                .sum()
            )

        aging_risk_count = 0
        if "durability_score" in merged.columns:
            aging_risk_count = int(
                pd.to_numeric(merged["durability_score"], errors="coerce")
                .fillna(100)
                .lt(55)
                .sum()
            )

        pick_count = len(team_picks)
        premium_pick_count = 0
        if not team_picks.empty and "round" in team_picks.columns:
            premium_pick_count = int(
                pd.to_numeric(team_picks["round"], errors="coerce")
                .fillna(99)
                .eq(1)
                .sum()
            )

        age_score = age_curve_score
        pick_score = min(100, 40 + pick_count * 5 + premium_pick_count * 12)
        cap_score = 50
        core_score = min(100, 35 + young_core_count * 8)

        team_dynasty_asset_score = round((engine_score * 0.45 + rank_score * 0.25 + age_curve_score * 0.30), 2)
        team_win_now_score = round((recent_score * 0.55 + career_score * 0.25 + engine_score * 0.20), 2)
        team_contract_value_score = 50
        team_risk_score = round(max(0, 100 - durability_score + aging_risk_count * 2), 2)

        risk_adjustment = min(35, team_risk_score * 0.25)

        future_score = (
            age_score * 0.18
            + pick_score * 0.18
            + cap_score * 0.10
            + core_score * 0.20
            + team_dynasty_asset_score * 0.22
            + team_contract_value_score * 0.12
            - risk_adjustment
        )

        future_score = round(max(0, min(100, future_score)), 2)

        rows.append({
            "league_id": app_league_id,
            "owner_team_name": owner_team_name,
            "owner_id": None,

            "roster_count": roster_count,
            "avg_age": 0,
            "avg_contract_years": 0,
            "avg_salary": 0,

            "young_core_count": young_core_count,
            "aging_risk_count": aging_risk_count,
            "draft_pick_count": pick_count,
            "premium_pick_count": premium_pick_count,

            "cap_space": 0,
            "cap_used": 0,

            "team_dynasty_asset_score": team_dynasty_asset_score,
            "team_win_now_score": team_win_now_score,
            "team_contract_value_score": team_contract_value_score,
            "team_risk_score": team_risk_score,

            "age_score": round(age_score, 2),
            "pick_score": round(pick_score, 2),
            "cap_score": round(cap_score, 2),
            "core_score": round(core_score, 2),

            "future_score": future_score,
            "future_grade": _grade(future_score),
            "team_window": _window(future_score, team_win_now_score),
        })

    df = pd.DataFrame(rows)

    print(f"Prepared team future context rows: {len(df)}")

    if not df.empty:
        sb.table(TARGET_TABLE).upsert(
            df.to_dict("records"),
            on_conflict="league_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(df)} team_future_context rows.")
    return df


if __name__ == "__main__":
    build_team_future_context()
