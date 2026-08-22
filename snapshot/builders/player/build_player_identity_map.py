from __future__ import annotations

import pandas as pd

from auth import service_client


TARGET_TABLE = "player_identity_map"


def build_player_identity_map():
    sb = service_client()

    print("Loading roster players...")
    roster_rows = (
        sb.table("rosters_current")
        .select("player_name,sleeper_id,pos,nfl_team")
        .execute()
        .data
        or []
    )

    print("Loading historical stat players...")
    stat_rows = (
        sb.table("player_season_stats")
        .select("player_name,sleeper_id,gsis_id,pos,team")
        .execute()
        .data
        or []
    )

    roster = pd.DataFrame(roster_rows)
    stats = pd.DataFrame(stat_rows)

    if roster.empty:
        print("No roster rows found.")
        return

    if stats.empty:
        print("No player season stat rows found.")
        return

    roster["join_name"] = roster["player_name"].str.lower().str.strip()
    stats["join_name"] = stats["player_name"].str.lower().str.strip()

    latest_stats = (
        stats.drop_duplicates(subset=["join_name", "pos"], keep="last")
    )

    merged = roster.merge(
        latest_stats,
        on=["join_name", "pos"],
        how="left",
        suffixes=("_sleeper", "_stats"),
    )

    rows = []

    for _, r in merged.iterrows():
        sleeper_id = r.get("sleeper_id_sleeper")
        gsis_id = r.get("gsis_id")
        stats_id = r.get("sleeper_id_stats")

        rows.append({
            "canonical_player_id": str(sleeper_id) if pd.notna(sleeper_id) else None,
            "sleeper_id": str(sleeper_id) if pd.notna(sleeper_id) else None,
            "gsis_id": str(gsis_id) if pd.notna(gsis_id) else (
                str(stats_id) if pd.notna(stats_id) else None
            ),
            "player_name": r.get("player_name_sleeper"),
            "pos": r.get("pos"),
            "nfl_team": r.get("nfl_team"),
            "matched_name": r.get("player_name_stats"),
            "match_source": "name_pos_match" if pd.notna(gsis_id) or pd.notna(stats_id) else "unmatched",
        })

    print(f"Prepared identity rows: {len(rows)}")

    if rows:
        sb.table(TARGET_TABLE).upsert(
            rows,
            on_conflict="canonical_player_id",
        ).execute()

    print(f"Upserted {len(rows)} player identity rows.")


if __name__ == "__main__":
    build_player_identity_map()
