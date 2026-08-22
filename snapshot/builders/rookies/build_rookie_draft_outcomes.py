from __future__ import annotations

import pandas as pd
import math
from auth import service_client


def safe_num(v, default=0.0):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except Exception:
        return default


def build_rookie_draft_outcomes():
    sb = service_client()

    draft_rows = (
        sb.table("rookie_draft_board")
        .select("*")
        .execute()
        .data
        or []
    )

    universe_rows = (
        sb.table("player_universe")
        .select("*")
        .execute()
        .data
        or []
    )

    universe_by_name = {
        str(r.get("player_name", "")).lower(): r
        for r in universe_rows
    }

    rows = []

    for d in draft_rows:
        player_name = d.get("player_name")
        u = universe_by_name.get(str(player_name).lower(), {})

        row = {
            "draft_year": d.get("class_year", 2026),
            "rookie_class_year": d.get("class_year", 2026),
            "class_year": d.get("class_year", 2026),
            "rookie_rank": d.get("rookie_rank"),
            "player_name": player_name,
            "pos": d.get("pos"),
            "season_ppg": safe_num(u.get("season_ppg")),
            "salary": safe_num(u.get("salary")),
        }

        rows.append(row)

    df = pd.DataFrame(rows)

    sb.table("rookie_draft_outcomes").upsert(
        df.to_dict("records"),
        on_conflict="class_year,player_name",
    ).execute()

    print(f"Upserted {len(df)} rookie_draft_outcomes rows")


if __name__ == "__main__":
    build_rookie_draft_outcomes()
