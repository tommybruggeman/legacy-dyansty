from __future__ import annotations

import pandas as pd


def build_teams_snapshot(ctx: dict) -> pd.DataFrame:
    owners = ctx.get("owners", pd.DataFrame()).copy()
    teams = ctx.get("teams", pd.DataFrame()).copy()
    roster_snapshot = ctx.get("roster_snapshot", pd.DataFrame()).copy()
    cap_snapshot = ctx.get("cap_snapshot", pd.DataFrame()).copy()
    if ctx.get("season") is None:
        raise ValueError("Snapshot context requires an authoritative season.")
    season = int(ctx["season"])

    if owners.empty:
        return pd.DataFrame()

    rows = []

    for _, owner in owners.iterrows():
        owner_name = str(owner.get("full_name", "") or "").strip()
        league_id = owner.get("league_id")
        owner_id = owner.get("id")

        owner_roster = pd.DataFrame()
        if not roster_snapshot.empty and "owner_name" in roster_snapshot.columns:
            owner_roster = roster_snapshot[
                roster_snapshot["owner_name"].astype(str).str.strip().eq(owner_name)
            ]

        owner_cap = pd.DataFrame()
        if not cap_snapshot.empty and "owner_name" in cap_snapshot.columns:
            owner_cap = cap_snapshot[
                cap_snapshot["owner_name"].astype(str).str.strip().eq(owner_name)
            ]

        cap_row = owner_cap.iloc[0] if not owner_cap.empty else {}

        rows.append(
            {
                "league_id": league_id,
                "season": season,
                "owner_id": owner_id,
                "owner_name": owner_name,
                "display_name": owner.get("display_name"),
                "sleeper_username": owner.get("sleeper_username"),
                "roster_size": int(len(owner_roster)),
                "qb_count": int((owner_roster.get("pos") == "QB").sum()) if not owner_roster.empty else 0,
                "rb_count": int((owner_roster.get("pos") == "RB").sum()) if not owner_roster.empty else 0,
                "wr_count": int((owner_roster.get("pos") == "WR").sum()) if not owner_roster.empty else 0,
                "te_count": int((owner_roster.get("pos") == "TE").sum()) if not owner_roster.empty else 0,
                "salary_cap": cap_row.get("salary_cap"),
                "active_salary": cap_row.get("active_salary"),
                "adjustment_total": cap_row.get("adjustment_total"),
                "cap_used": cap_row.get("cap_used"),
                "cap_space": cap_row.get("cap_space"),
                "is_over_cap": cap_row.get("is_over_cap"),
            }
        )

    return pd.DataFrame(rows)
