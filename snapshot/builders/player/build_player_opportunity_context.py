from __future__ import annotations

from datetime import datetime, timezone
import math
import pandas as pd

from auth import service_client

TARGET_TABLE = "player_opportunity_context"


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


def grade(score):
    if score >= 80:
        return "ELITE_OPPORTUNITY"
    if score >= 65:
        return "STRONG_OPPORTUNITY"
    if score >= 50:
        return "USABLE_OPPORTUNITY"
    if score >= 35:
        return "FRAGILE_OPPORTUNITY"
    return "POOR_OPPORTUNITY"


def build_player_opportunity_context():
    sb = service_client()

    print("Loading role context...")
    role = pd.DataFrame(fetch_all(
        sb,
        "player_role_context",
        "sleeper_id,owner_team_name,player_name,pos,nfl_team,role_score,projected_volume_score,competition_risk_score,breakout_path_score,role_security_tier"
    ))

    print("Loading situation context...")
    situation = pd.DataFrame(fetch_all(
        sb,
        "player_situation_context",
        "sleeper_id,owner_team_name,situation_score,team_environment_score,qb_environment_score,scheme_fit_score,offensive_line_score,ol_dependency_score,ol_adjusted_impact_score"
    ))

    print("Loading team NFL context...")
    team = pd.DataFrame(fetch_all(
        sb,
        "team_nfl_context",
        "nfl_team,scheme_label,offensive_environment_score,qb_environment_score,rushing_environment_score,pass_rate,rush_rate"
    ))

    print("Loading historical OL context...")
    ol = pd.DataFrame(fetch_all(
        sb,
        "offensive_line_context",
        "nfl_team,offensive_line_score,pass_protection_score,run_blocking_score"
    ))

    print("Loading future team context...")
    future = pd.DataFrame(fetch_all(
        sb,
        "team_future_context",
        "nfl_team,projected_offensive_line_score,projected_pass_protection_score,projected_run_blocking_score,projected_scheme_label,projected_pass_rate,projected_rush_rate,future_context_confidence,future_context_note"
    ))

    if role.empty:
        print("No player_role_context rows found.")
        return

    role["sleeper_id"] = role["sleeper_id"].astype(str)
    df = role.copy()

    if not situation.empty:
        situation["sleeper_id"] = situation["sleeper_id"].astype(str)
        df = df.merge(
            situation,
            on=["sleeper_id", "owner_team_name"],
            how="left",
        )

    if not team.empty:
        df = df.merge(team, on="nfl_team", how="left")

    if not ol.empty:
        df = df.merge(ol, on="nfl_team", how="left", suffixes=("", "_hist"))

    if not future.empty:
        df = df.merge(future, on="nfl_team", how="left")

    rows = []

    for _, r in df.iterrows():
        sleeper_id = str(r.get("sleeper_id"))
        owner = r.get("owner_team_name")
        name = r.get("player_name")
        pos = r.get("pos")
        nfl_team = r.get("nfl_team") or "FA"

        role_score = _num(r.get("role_score"), 50)
        projected_volume = _num(r.get("projected_volume_score"), 50)
        competition_risk = _num(r.get("competition_risk_score"), 50)
        breakout_path = _num(r.get("breakout_path_score"), 50)

        situation_score = _num(r.get("situation_score"), 50)
        team_env = _num(r.get("team_environment_score"), _num(r.get("offensive_environment_score"), 50))
        qb_env = _num(r.get("qb_environment_score"), 50)
        scheme_fit = _num(r.get("scheme_fit_score"), 50)
        ol_dependency = _num(r.get("ol_dependency_score"), 50)

        historical_ol = _num(r.get("offensive_line_score_hist"), _num(r.get("offensive_line_score"), 50))
        historical_pass = _num(r.get("pass_protection_score"), 50)
        historical_run = _num(r.get("run_blocking_score"), 50)

        future_conf = _num(r.get("future_context_confidence"), 0)

        projected_ol_raw = _num(r.get("projected_offensive_line_score"), historical_ol)
        projected_pass_raw = _num(r.get("projected_pass_protection_score"), historical_pass)
        projected_run_raw = _num(r.get("projected_run_blocking_score"), historical_run)

        # Future context is confidence-weighted.
        projected_ol = clamp(
            historical_ol * (1 - future_conf / 100)
            + projected_ol_raw * (future_conf / 100)
        )
        projected_pass = clamp(
            historical_pass * (1 - future_conf / 100)
            + projected_pass_raw * (future_conf / 100)
        )
        projected_run = clamp(
            historical_run * (1 - future_conf / 100)
            + projected_run_raw * (future_conf / 100)
        )

        ol_change = projected_ol - historical_ol

        if pos == "RB":
            line_opportunity = clamp(projected_run * 0.65 + projected_ol * 0.25 + team_env * 0.10)
            position_env = clamp(line_opportunity * 0.45 + projected_volume * 0.35 + scheme_fit * 0.20)
        elif pos == "QB":
            line_opportunity = clamp(projected_pass * 0.65 + projected_ol * 0.20 + qb_env * 0.15)
            position_env = clamp(line_opportunity * 0.35 + qb_env * 0.35 + projected_volume * 0.30)
        elif pos in ["WR", "TE"]:
            line_opportunity = clamp(projected_pass * 0.35 + qb_env * 0.45 + projected_ol * 0.20)
            position_env = clamp(line_opportunity * 0.30 + projected_volume * 0.35 + scheme_fit * 0.20 + qb_env * 0.15)
        else:
            line_opportunity = projected_ol
            position_env = clamp(projected_volume * 0.50 + situation_score * 0.50)

        opportunity_score = clamp(
            role_score * 0.25
            + projected_volume * 0.25
            + situation_score * 0.15
            + position_env * 0.20
            + breakout_path * 0.15
            - competition_risk * 0.05
        )

        floor_score = clamp(
            role_score * 0.35
            + projected_volume * 0.30
            + situation_score * 0.20
            + position_env * 0.15
            - competition_risk * 0.10
        )

        ceiling_score = clamp(
            breakout_path * 0.30
            + projected_volume * 0.25
            + position_env * 0.25
            + max(0, ol_change) * (ol_dependency / 100) * 0.20
            + role_score * 0.20
        )

        breakout_probability = clamp(
            ceiling_score * 0.35
            + breakout_path * 0.30
            + projected_volume * 0.20
            + max(0, ol_change) * 0.15
            - competition_risk * 0.10
        )

        flag = None
        if nfl_team == "FA":
            flag = "NO_TEAM_OPPORTUNITY_RISK"
        elif opportunity_score >= 65 and ol_change >= 10:
            flag = "IMPROVING_ENVIRONMENT_BREAKOUT_PATH"
        elif role_score >= 65 and situation_score < 40:
            flag = "GOOD_ROLE_BAD_ENVIRONMENT"
        elif projected_volume >= 65 and opportunity_score < 45:
            flag = "VOLUME_WITH_ENVIRONMENT_RISK"
        elif competition_risk >= 70:
            flag = "OPPORTUNITY_COMPETITION_RISK"

        future_note = r.get("future_context_note")
        note = (
            f"Machine-derived opportunity from role, projected volume, situation, team/QB environment, "
            f"scheme fit, historical OL, confidence-weighted projected OL, competition risk, and breakout path. "
            f"Historical OL={round(historical_ol, 2)}, projected OL={round(projected_ol, 2)}, "
            f"OL change={round(ol_change, 2)}, future confidence={round(future_conf, 2)}. "
            f"Future note={future_note}."
        )

        rows.append({
            "sleeper_id": sleeper_id,
            "owner_team_name": owner,
            "player_name": name,
            "pos": pos,
            "nfl_team": nfl_team,

            "role_score": clean(round(role_score, 2)),
            "projected_volume_score": clean(round(projected_volume, 2)),
            "opportunity_score": clean(round(opportunity_score, 2)),
            "floor_score": clean(round(floor_score, 2)),
            "ceiling_score": clean(round(ceiling_score, 2)),
            "breakout_probability_score": clean(round(breakout_probability, 2)),

            "historical_ol_score": clean(round(historical_ol, 2)),
            "projected_ol_score": clean(round(projected_ol, 2)),
            "ol_change_score": clean(round(ol_change, 2)),

            "opportunity_grade": grade(opportunity_score),
            "opportunity_flag": flag,
            "opportunity_note": note,

            "source": "player_opportunity_context_v1",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })

    print(f"Prepared opportunity rows: {len(rows)}")

    clean_rows = [
        {k: clean(v) for k, v in row.items()}
        for row in rows
    ]

    sb.table(TARGET_TABLE).upsert(
        clean_rows,
        on_conflict="sleeper_id,owner_team_name",
    ).execute()

    print(f"Upserted {len(clean_rows)} rows into {TARGET_TABLE}")


if __name__ == "__main__":
    build_player_opportunity_context()
