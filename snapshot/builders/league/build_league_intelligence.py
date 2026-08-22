from __future__ import annotations

import math
import pandas as pd

from auth import service_client


def pct(series):
    return series.rank(pct=True, ascending=True) * 100


def clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return 0
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v


def rank_desc(series):
    return series.rank(ascending=False, method="min").astype(int)


def league_label(rank: int, teams: int) -> str:
    if rank == 1:
        return "CHAMPIONSHIP FAVORITE"
    if rank <= 3:
        return "CONTENDER"
    if rank <= max(4, teams // 2):
        return "PLAYOFF TEAM"
    if rank <= 8:
        return "BUBBLE TEAM"
    return "RETOOL"


def timeline_label(row) -> str:
    if row["overall_rank"] <= 3 and row["future_percentile"] >= 60:
        return "YOUNG CONTENDER"
    if row["overall_rank"] <= 3 and row["future_percentile"] < 45:
        return "ALL-IN"
    if row["overall_rank"] <= 5 and row["cap_percentile"] <= 30:
        return "WIN-NOW CAP PRESSURE"
    if row["future_percentile"] >= 80 and row["overall_rank"] >= 6:
        return "ASCENDING"
    if row["cap_percentile"] <= 20:
        return "CAP CONSTRAINED"
    return "TRANSITIONING"


def trade_strategy(row) -> str:
    if row["overall_rank"] <= 3:
        if row["cap_percentile"] <= 30:
            return "BUY CAREFULLY: contender with limited cap flexibility"
        return "BUY: push for production upgrades"
    if row["overall_rank"] <= 5:
        return "SELECTIVE BUYER: improve weak spots without draining future"
    if row["future_percentile"] >= 75:
        return "PATIENT BUILDER: use future strength to buy selectively"
    if row["cap_percentile"] <= 25:
        return "SELL/RESET: clear bad money and rebalance roster"
    return "OPPORTUNISTIC: explore value trades both directions"


def main():
    sb = service_client()

    teams = pd.DataFrame(
        sb.table("team_window_scores")
        .select("*")
        .execute()
        .data
        or []
    )

    if teams.empty:
        print("No team window scores.")
        return

    teams["window_score"] = pd.to_numeric(teams["window_score"], errors="coerce").fillna(0)
    teams["future_score"] = pd.to_numeric(teams["future_score"], errors="coerce").fillna(0)
    teams["cap_health_score"] = pd.to_numeric(teams["cap_health_score"], errors="coerce").fillna(0)
    teams["depth_score"] = pd.to_numeric(teams["depth_score"], errors="coerce").fillna(0)

    teams["overall_rank"] = rank_desc(teams["window_score"])
    teams["window_percentile"] = pct(teams["window_score"])
    teams["future_percentile"] = pct(teams["future_score"])
    teams["cap_percentile"] = pct(teams["cap_health_score"])
    teams["depth_percentile"] = pct(teams["depth_score"])

    for pos in ["QB", "RB", "WR", "TE"]:
        score_col = f"{pos.lower()}_score"
        rank_col = f"{pos.lower()}_rank"

        def pos_score(summary):
            try:
                return float((summary or {}).get(pos, {}).get("win_now", 0))
            except Exception:
                return 0

        teams[score_col] = teams["positional_summary"].apply(pos_score)
        teams[rank_col] = rank_desc(teams[score_col])

    total_teams = len(teams)
    rows = []

    for _, r in teams.iterrows():
        strengths = []
        weaknesses = []

        for pos in ["QB", "RB", "WR", "TE"]:
            rank = int(r[f"{pos.lower()}_rank"])

            if rank <= 3:
                strengths.append(f"{pos} room ranks {rank}/{total_teams}")
            elif rank >= 8:
                weaknesses.append(f"{pos} room ranks {rank}/{total_teams}")

        if r["cap_percentile"] >= 75:
            strengths.append("strong cap position")
        elif r["cap_percentile"] <= 30:
            weaknesses.append("limited cap flexibility")

        if r["future_percentile"] >= 75:
            strengths.append("strong future core")
        elif r["future_percentile"] <= 30:
            weaknesses.append("thin future profile")

        row = {
            "owner_team_name": r["owner_team_name"],
            "overall_rank": int(r["overall_rank"]),
            "window_percentile": round(float(r["window_percentile"]), 2),
            "future_percentile": round(float(r["future_percentile"]), 2),
            "cap_percentile": round(float(r["cap_percentile"]), 2),
            "depth_percentile": round(float(r["depth_percentile"]), 2),
            "qb_rank": int(r["qb_rank"]),
            "rb_rank": int(r["rb_rank"]),
            "wr_rank": int(r["wr_rank"]),
            "te_rank": int(r["te_rank"]),
            "league_window_label": league_label(int(r["overall_rank"]), total_teams),
            "league_timeline_label": timeline_label(r),
            "strengths": strengths,
            "weaknesses": weaknesses,
            "trade_strategy": trade_strategy(r),
        }

        rows.append(clean(row))

    sb.table("league_intelligence").upsert(
        rows,
        on_conflict="owner_team_name",
    ).execute()

    print(f"Upserted league intelligence rows: {len(rows)}")

    out = pd.DataFrame(rows).sort_values("overall_rank")
    print(
        out[
            [
                "owner_team_name",
                "overall_rank",
                "league_window_label",
                "league_timeline_label",
                "qb_rank",
                "rb_rank",
                "wr_rank",
                "te_rank",
                "trade_strategy",
            ]
        ]
    )


if __name__ == "__main__":
    main()
