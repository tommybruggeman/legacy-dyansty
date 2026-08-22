from __future__ import annotations

import math
import pandas as pd

from auth import service_client


TARGET_TABLE = "player_situation_context"

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


def situation_grade(score: float) -> str:
    if score >= 80:
        return "ELITE_SITUATION"
    if score >= 65:
        return "GOOD_SITUATION"
    if score >= 50:
        return "NEUTRAL_SITUATION"
    if score >= 35:
        return "RISKY_SITUATION"
    return "BAD_SITUATION"



def clean_str(v):
    v = clean_json_value(v)
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


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


def build_situation_context():
    sb = service_client()

    print("Loading player action brain...")
    brain = pd.DataFrame(
        sb.table("player_action_brain").select("*").execute().data or []
    )

    print("Loading future projections...")
    future = pd.DataFrame(
        sb.table("player_future_projection").select("*").execute().data or []
    )

    print("Loading NFL context...")
    nfl = pd.DataFrame(
        fetch_all(
            sb,
            "player_nfl_context",
            "sleeper_id,player_name,pos,nfl_team,status,active,age,years_exp,depth_chart_order,injury_status",
        )
    )
    print(f"Loaded NFL context rows: {len(nfl)}")

    print("Loading team NFL context...")
    team_ctx = pd.DataFrame(
        fetch_all(
            sb,
            "team_nfl_context",
            "nfl_team,scheme_label,offensive_environment_score,qb_environment_score,rushing_environment_score,pace_score,pass_rate,rush_rate",
        )
    )
    print(f"Loaded team NFL context rows: {len(team_ctx)}")

    print("Loading offensive line context...")
    ol_ctx = pd.DataFrame(
        fetch_all(
            sb,
            "offensive_line_context",
            "nfl_team,pass_protection_score,run_blocking_score,offensive_line_score",
        )
    )
    print(f"Loaded offensive line context rows: {len(ol_ctx)}")

    if brain.empty:
        print("No player_action_brain rows found.")
        return

    brain["sleeper_id"] = brain["sleeper_id"].astype(str)

    df = brain.copy()

    if not future.empty:
        future["sleeper_id"] = future["sleeper_id"].astype(str)
        df = df.merge(
            future[
                [
                    "sleeper_id",
                    "future_value_score",
                    "future_production_score",
                    "sample_confidence",
                    "projection_tier",
                ]
            ],
            on="sleeper_id",
            how="left",
            suffixes=("", "_future"),
        )

    if not nfl.empty:
        nfl["sleeper_id"] = nfl["sleeper_id"].astype(str)
        df = df.merge(
            nfl[
                [
                    "sleeper_id",
                    "nfl_team",
                    "status",
                    "active",
                    "age",
                    "years_exp",
                    "depth_chart_order",
                    "injury_status",
                ]
            ],
            on="sleeper_id",
            how="left",
            suffixes=("", "_nfl"),
        )

    if not team_ctx.empty:
        df = df.merge(
            team_ctx[
                [
                    "nfl_team",
                    "scheme_label",
                    "offensive_environment_score",
                    "qb_environment_score",
                    "rushing_environment_score",
                    "pace_score",
                    "pass_rate",
                    "rush_rate",
                ]
            ],
            on="nfl_team",
            how="left",
        )

    if not ol_ctx.empty:
        df = df.merge(
            ol_ctx[
                [
                    "nfl_team",
                    "pass_protection_score",
                    "run_blocking_score",
                    "offensive_line_score",
                ]
            ],
            on="nfl_team",
            how="left",
        )

    rows = []

    for _, r in df.iterrows():
        name = r.get("player_name")
        pos = r.get("pos")

        nfl_team = clean_str(r.get("nfl_team")) or "FA"
        nfl_status = clean_str(r.get("status"))
        nfl_active = clean_json_value(r.get("active"))
        age = _num(r.get("age"), None)
        years_exp = _num(r.get("years_exp"), None)
        depth_order = _num(r.get("depth_chart_order"), None)
        injury_status = clean_str(r.get("injury_status"))

        market = _num(r.get("market_score"), 50)
        performance = _num(r.get("performance_score"), 50)
        future_score = _num(r.get("future_value_score"), _num(r.get("future_score"), 50))
        confidence = _num(r.get("sample_confidence"), 0)
        salary = _num(r.get("salary"), 0)
        years = _num(r.get("years"), 0)

        role_security_score = clamp(
            performance * 0.35
            + market * 0.30
            + future_score * 0.25
            + confidence * 0.10
        )

        # NFL roster/depth metadata adjustment
        if nfl_active is False:
            role_security_score -= 15

        if depth_order is not None:
            if depth_order <= 1:
                role_security_score += 8
            elif depth_order == 2:
                role_security_score += 2
            elif depth_order >= 3:
                role_security_score -= min(20, depth_order * 4)

        if injury_status:
            role_security_score -= 8

        role_security_score = clamp(role_security_score)

        depth_chart_pressure_score = clamp(100 - role_security_score)

        team_environment_score = _num(
            r.get("offensive_environment_score"),
            performance * 0.45 + future_score * 0.35 + market * 0.20,
        )

        qb_environment_score = _num(r.get("qb_environment_score"), 50.0)
        rushing_environment_score = _num(r.get("rushing_environment_score"), 50.0)
        scheme_label = clean_str(r.get("scheme_label")) or "UNKNOWN"
        pass_rate = _num(r.get("pass_rate"), 0.55)
        rush_rate = _num(r.get("rush_rate"), 0.40)

        # Position/scheme fit. This is intentionally simple for now.
        if pos in ["WR", "TE"]:
            scheme_fit_score = clamp(50 + (pass_rate - 0.55) * 100)
        elif pos == "RB":
            scheme_fit_score = clamp(50 + (rush_rate - 0.40) * 100)
        elif pos == "QB":
            scheme_fit_score = clamp(50 + (pass_rate - 0.55) * 80)
        else:
            scheme_fit_score = 50.0

        pass_protection_score = _num(r.get("pass_protection_score"), 50.0)
        run_blocking_score = _num(r.get("run_blocking_score"), 50.0)
        offensive_line_score = _num(r.get("offensive_line_score"), 50.0)

        # OL dependency is player-context-aware, not one-size-fits-all.
        # RBs depend most on run blocking and volume.
        # Pocket QBs depend more on pass protection.
        # WR/TE are affected mostly through QB time/efficiency, less directly.
        # Young players are slightly more environment-sensitive than established vets.
        age_adj = 0
        if age is not None:
            if age <= 24:
                age_adj = 5
            elif age >= 30:
                age_adj = -3

        if pos == "RB":
            ol_dependency_score = clamp(70 + max(0, rush_rate - 0.40) * 50 + age_adj)
            ol_adjusted_impact_score = clamp(
                run_blocking_score * 0.70
                + offensive_line_score * 0.20
                + rushing_environment_score * 0.10
            )
        elif pos == "QB":
            # We will later improve this using rushing profile / pressure sensitivity.
            ol_dependency_score = clamp(55 + age_adj)
            ol_adjusted_impact_score = clamp(
                pass_protection_score * 0.70
                + qb_environment_score * 0.20
                + (
                offensive_line_score * (1 - ol_dependency_score / 100)
                + ol_adjusted_impact_score * (ol_dependency_score / 100)
            ) * 0.10
            )
        elif pos in ["WR", "TE"]:
            ol_dependency_score = clamp(35 + max(0, pass_rate - 0.55) * 40 + age_adj)
            ol_adjusted_impact_score = clamp(
                pass_protection_score * 0.45
                + qb_environment_score * 0.40
                + offensive_line_score * 0.15
            )
        else:
            ol_dependency_score = 50.0
            ol_adjusted_impact_score = offensive_line_score

        situation_risk_score = clamp(
            depth_chart_pressure_score * 0.45
            + (100 - confidence) * 0.25
            + max(0, salary - 20) * 0.75
            + max(0, years - 2) * 5
        )

        if nfl_team == "FA":
            situation_risk_score += 20

        if injury_status:
            situation_risk_score += 10

        situation_risk_score = clamp(situation_risk_score)

        situation_score = clamp(
            role_security_score * 0.35
            + team_environment_score * 0.25
            + scheme_fit_score * 0.15
            + qb_environment_score * 0.15
            + (
                offensive_line_score * (1 - ol_dependency_score / 100)
                + ol_adjusted_impact_score * (ol_dependency_score / 100)
            ) * 0.10
            - situation_risk_score * 0.20
        )

        risk_flag = None
        note = (
            f"Machine-derived from market, performance, future projection, confidence, "
            f"NFL team/status, depth chart order, injury status, salary, contract years, "
            f"team offensive environment, QB environment, rushing environment, pace, scheme, "
            f"offensive line quality, and player-specific OL dependency. "
            f"NFL context: {nfl_team}, status={nfl_status}, depth={depth_order}, injury={injury_status}, "
            f"scheme={scheme_label}, pass_rate={round(pass_rate, 3)}, rush_rate={round(rush_rate, 3)}."
        )

        if nfl_team == "FA":
            risk_flag = "FREE_AGENT_ROSTER_RISK"
        elif injury_status:
            risk_flag = "INJURY_SITUATION_RISK"
        elif pos == "QB" and role_security_score < 45:
            risk_flag = "STARTING_JOB_RISK"
        elif pos in ["RB", "WR", "TE"] and confidence < 20 and future_score < 55:
            risk_flag = "UNCERTAIN_ROLE"
        elif situation_risk_score >= 70:
            risk_flag = "HIGH_SITUATION_RISK"

        rows.append({
            "sleeper_id": str(r.get("sleeper_id")),
            "player_name": name,
            "owner_team_name": r.get("owner_team_name"),
            "pos": pos,

            "nfl_team": nfl_team,
            "nfl_status": nfl_status,
            "nfl_active": nfl_active,
            "age": age,
            "years_exp": years_exp,
            "depth_chart_order": depth_order,
            "injury_status": injury_status,

            "role_security_score": round(role_security_score, 2),
            "depth_chart_pressure_score": round(depth_chart_pressure_score, 2),
            "team_environment_score": round(team_environment_score, 2),
            "scheme_fit_score": round(scheme_fit_score, 2),
            "qb_environment_score": round(qb_environment_score, 2),
            "offensive_line_score": round(offensive_line_score, 2),
            "pass_protection_score": round(pass_protection_score, 2),
            "run_blocking_score": round(run_blocking_score, 2),
            "ol_dependency_score": round(ol_dependency_score, 2),
            "ol_adjusted_impact_score": round(ol_adjusted_impact_score, 2),

            "situation_risk_score": round(situation_risk_score, 2),
            "situation_score": round(situation_score, 2),
            "situation_grade": situation_grade(situation_score),
            "situation_risk_flag": risk_flag,
            "situation_note": note,
        })

    print(f"Prepared situation context rows: {len(rows)}")

    if rows:
        clean_rows = [
            {k: clean_json_value(v) for k, v in row.items()}
            for row in rows
        ]

        sb.table(TARGET_TABLE).upsert(
            clean_rows,
            on_conflict="sleeper_id,owner_team_name",
        ).execute()

    print(f"Upserted {len(rows)} player_situation_context rows.")


if __name__ == "__main__":
    build_situation_context()
