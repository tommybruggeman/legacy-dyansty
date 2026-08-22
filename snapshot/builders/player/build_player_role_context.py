from __future__ import annotations

from datetime import datetime, timezone
import math
import pandas as pd

from auth import service_client

TARGET_TABLE = "player_role_context"

def clean_json_value(v):
    if v is None:
        return None

    if isinstance(v, dict):
        return {k: clean_json_value(val) for k, val in v.items()}

    if isinstance(v, list):
        return [clean_json_value(x) for x in v]

    try:
        if pd.isna(v):
            return None
    except Exception:
        pass

    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None

    return v



def clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _num(v, default=0.0):
    try:
        x = pd.to_numeric(v, errors="coerce")
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, float(v)))


def fetch_all(sb, table, select_cols, page_size=1000):
    rows = []
    start = 0
    while True:
        batch = (
            sb.table(table)
            .select(select_cols)
            .range(start, start + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def role_tier(score):
    if score >= 80:
        return "LOCKED_IN_ROLE"
    if score >= 65:
        return "STRONG_ROLE"
    if score >= 50:
        return "USABLE_ROLE"
    if score >= 35:
        return "FRAGILE_ROLE"
    return "WEAK_ROLE"


def build_player_role_context():
    sb = service_client()

    print("Loading player action brain...")
    brain = pd.DataFrame(fetch_all(
        sb,
        "player_action_brain",
        "sleeper_id,player_name,owner_team_name,pos,market_score,performance_score,salary,years"
    ))

    print("Loading NFL player context...")
    nfl = pd.DataFrame(fetch_all(
        sb,
        "player_nfl_context",
        "sleeper_id,nfl_team,active,age,years_exp,depth_chart_order,status,injury_status"
    ))

    print("Loading situation context...")
    situation = pd.DataFrame(fetch_all(
        sb,
        "player_situation_context",
        "sleeper_id,owner_team_name,team_environment_score,qb_environment_score,scheme_fit_score,offensive_line_score,ol_dependency_score,ol_adjusted_impact_score"
    ))

    if brain.empty:
        print("No player_action_brain rows found.")
        return

    brain["sleeper_id"] = brain["sleeper_id"].astype(str)
    df = brain.copy()

    if not nfl.empty:
        nfl["sleeper_id"] = nfl["sleeper_id"].astype(str)
        df = df.merge(nfl, on="sleeper_id", how="left")

    if not situation.empty:
        situation["sleeper_id"] = situation["sleeper_id"].astype(str)
        df = df.merge(
            situation,
            on=["sleeper_id", "owner_team_name"],
            how="left",
            suffixes=("", "_situation")
        )

    rows = []

    for _, r in df.iterrows():
        sleeper_id = str(r.get("sleeper_id"))
        name = r.get("player_name")
        owner = r.get("owner_team_name")
        pos = r.get("pos")
        nfl_team = r.get("nfl_team") or "FA"

        depth = _num(r.get("depth_chart_order"), None)
        active = clean(r.get("active"))
        age = _num(r.get("age"), None)
        years_exp = _num(r.get("years_exp"), None)

        market = _num(r.get("market_score"), 50)
        performance = _num(r.get("performance_score"), 50)
        salary = _num(r.get("salary"), 0)

        team_env = _num(r.get("team_environment_score"), 50)
        qb_env = _num(r.get("qb_environment_score"), 50)
        scheme_fit = _num(r.get("scheme_fit_score"), 50)
        ol_impact = _num(r.get("ol_adjusted_impact_score"), _num(r.get("offensive_line_score"), 50))

        if depth is None:
            depth_score = 35
        elif depth <= 1:
            depth_score = 90
        elif depth == 2:
            depth_score = 62
        elif depth == 3:
            depth_score = 42
        else:
            depth_score = 25

        if active is False:
            depth_score -= 20

        if pos == "QB":
            projected_volume_score = clamp(
                depth_score * 0.55
                + qb_env * 0.20
                + team_env * 0.15
                + market * 0.10
            )
            competition_risk_score = clamp(100 - depth_score + max(0, 50 - market) * 0.4)

        elif pos == "RB":
            projected_volume_score = clamp(
                depth_score * 0.45
                + scheme_fit * 0.20
                + ol_impact * 0.20
                + team_env * 0.15
            )
            competition_risk_score = clamp(100 - depth_score + max(0, salary - 15) * 0.5)

        elif pos in ["WR", "TE"]:
            projected_volume_score = clamp(
                depth_score * 0.40
                + qb_env * 0.25
                + scheme_fit * 0.20
                + market * 0.15
            )
            competition_risk_score = clamp(100 - depth_score + max(0, 45 - qb_env) * 0.35)

        else:
            projected_volume_score = clamp(depth_score)
            competition_risk_score = clamp(100 - depth_score)

        age_curve_boost = 0
        if age is not None:
            if pos == "RB" and age <= 24:
                age_curve_boost = 8
            elif pos in ["WR", "TE"] and age <= 25:
                age_curve_boost = 6
            elif pos == "QB" and 25 <= age <= 33:
                age_curve_boost = 5
            elif pos == "RB" and age >= 28:
                age_curve_boost = -8
            elif pos in ["WR", "TE"] and age >= 31:
                age_curve_boost = -6
            elif pos == "QB" and age >= 36:
                age_curve_boost = -5

        role_score = clamp(
            projected_volume_score * 0.45
            + market * 0.20
            + performance * 0.15
            + depth_score * 0.15
            + age_curve_boost
        )

        breakout_path_score = clamp(
            (100 - competition_risk_score) * 0.30
            + projected_volume_score * 0.35
            + scheme_fit * 0.15
            + ol_impact * 0.10
            + max(0, 55 - market) * 0.10
        )

        risk_flag = None
        if nfl_team == "FA":
            risk_flag = "NO_NFL_TEAM"
        elif active is False:
            risk_flag = "INACTIVE_ROSTER_STATUS"
        elif depth is not None and depth >= 3:
            risk_flag = "DEPTH_CHART_BLOCKED"
        elif competition_risk_score >= 70:
            risk_flag = "HIGH_COMPETITION_RISK"

        note = (
            f"Machine-derived role context from depth chart order, active status, age, "
            f"market/performance scores, team environment, QB environment, scheme fit, "
            f"and offensive line adjusted impact. NFL team={nfl_team}, depth={depth}, "
            f"active={active}, projected_volume={round(projected_volume_score, 2)}, "
            f"competition_risk={round(competition_risk_score, 2)}."
        )

        rows.append({
            "sleeper_id": sleeper_id,
            "owner_team_name": owner,
            "player_name": name,
            "pos": pos,
            "nfl_team": nfl_team,

            "depth_chart_order": clean(depth),
            "nfl_active": clean(active),
            "age": clean(age),
            "years_exp": clean(years_exp),

            "role_score": clean(round(role_score, 2)),
            "projected_volume_score": clean(round(projected_volume_score, 2)),
            "competition_risk_score": clean(round(competition_risk_score, 2)),
            "breakout_path_score": clean(round(breakout_path_score, 2)),
            "role_security_tier": role_tier(role_score),
            "role_risk_flag": risk_flag,
            "role_note": note,

            "source": "player_role_context_v1",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })

    print(f"Prepared role context rows: {len(rows)}")

    clean_rows = [
        {k: clean_json_value(v) for k, v in row.items()}
        for row in rows
    ]

    sb.table(TARGET_TABLE).upsert(
        clean_rows,
        on_conflict="sleeper_id,owner_team_name",
    ).execute()

    print(f"Upserted {len(rows)} rows into {TARGET_TABLE}")


if __name__ == "__main__":
    build_player_role_context()
