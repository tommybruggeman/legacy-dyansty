# Legacy App/lib/standings.py
from __future__ import annotations
from collections import defaultdict
import pandas as pd

def build_standings_from_df(
    df: pd.DataFrame,
    tie_points: int = 0,
    median_tie_counts: bool = True,
) -> pd.DataFrame:
    """
    Custom standings:
      - 2 pts for a head-to-head win
      - 1 pt for beating the weekly median (>= if median_tie_counts=True)
      - tie_points for a head-to-head tie (usually 0)
    Input columns (one row per team-game):
      week | matchup_id | roster_id | points | opponent_roster_id | owner_name
    Output:
      Team, W, L, T, MB, PF, PA, AvgPF, AvgPA, TotalPts
    """
    if df.empty:
        return pd.DataFrame(
            columns=["Team","W","L","T","MB","PF","PA","AvgPF","AvgPA","TotalPts"]
        )

    week_medians = df.groupby("week")["points"].median().to_dict()

    df = df.copy()
    key = list(zip(df.week, df.matchup_id, df.roster_id))
    lookup = {(w, m, r): p for (w, m, r), p in zip(key, df.points)}
    df["PA"] = [
        lookup.get((w, m, o), 0.0) if o is not None else 0.0
        for w, m, o in zip(df.week, df.matchup_id, df.opponent_roster_id)
    ]

    stats = defaultdict(lambda: {
        "Team": None, "W": 0, "L": 0, "T": 0, "MB": 0, "PF": 0.0, "PA": 0.0, "G": 0
    })

    for _, row in df.iterrows():
        team = row["owner_name"]
        pf = float(row["points"] or 0.0)
        pa = float(row["PA"] or 0.0)
        median = float(week_medians.get(int(row["week"]), 0.0))

        s = stats[team]
        s["Team"] = team
        s["PF"] += pf
        s["PA"] += pa
        s["G"] += 1

        if row["opponent_roster_id"] is not None and row["matchup_id"] not in (None, -1):
            if pf > pa: s["W"] += 1
            elif pf < pa: s["L"] += 1
            else: s["T"] += 1

        if (pf >= median) if median_tie_counts else (pf > median):
            s["MB"] += 1

    rows = []
    for s in stats.values():
        wins, ties = s["W"], s["T"]
        h2h_points = 2 * wins + tie_points * ties
        total_pts = h2h_points + s["MB"]
        g = max(1, s["G"])
        rows.append({
            "Team": s["Team"],
            "W": s["W"], "L": s["L"], "T": s["T"],
            "MB": s["MB"],
            "PF": round(s["PF"], 2),
            "PA": round(s["PA"], 2),
            "AvgPF": round(s["PF"] / g, 2),
            "AvgPA": round(s["PA"] / g, 2),
            "TotalPts": int(total_pts),
        })

    out = pd.DataFrame(rows).sort_values(
        by=["TotalPts", "PF", "AvgPF"],
        ascending=[False, False, False],
        kind="mergesort",
    )
    return out.reset_index(drop=True)
