from __future__ import annotations

import pandas as pd


def _num(series, default=0.0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def build_roster_snapshot(ctx: dict) -> pd.DataFrame:
    contracts = ctx.get("contracts", pd.DataFrame()).copy()
    teams = ctx.get("teams", pd.DataFrame()).copy()
    owners = ctx.get("owners", pd.DataFrame()).copy()
    player_engine_scores = ctx.get("player_engine_scores", pd.DataFrame()).copy()
    if ctx.get("season") is None:
        raise ValueError("Snapshot context requires an authoritative season.")
    season = int(ctx["season"])

    if contracts.empty:
        return pd.DataFrame()

    owner_id_by_name = {}
    display_name_by_name = {}

    if not owners.empty:
        for _, owner in owners.iterrows():
            for name_field in ["full_name", "display_name", "team_name"]:
                owner_name = owner.get(name_field)
                if owner_name:
                    clean_name = str(owner_name).strip()
                    owner_id_by_name[clean_name] = owner.get("id")
                    display_name_by_name[clean_name] = owner.get("display_name") or clean_name

    rows = []

    for _, row in contracts.iterrows():
        sleeper_id = str(row.get("sleeper_id", "") or "").strip()
        owner_name = str(row.get("owner", "") or "").strip()

        player_match = pd.DataFrame()
        if not player_engine_scores.empty and "sleeper_id" in player_engine_scores.columns:
            player_match = player_engine_scores[
                player_engine_scores["sleeper_id"].astype(str).str.strip().eq(sleeper_id)
            ]

        engine_score = None
        engine_tier = None
        engine_summary = None

        if not player_match.empty:
            player_row = player_match.iloc[0]
            engine_score = player_row.get("engine_score")
            engine_tier = player_row.get("engine_tier")
            engine_summary = player_row.get("engine_summary")

        salary = float(row.get("salary") or 0)
        years_left = int(float(row.get("years") or 0))
        total_years = int(float(row.get("contract_total_years") or years_left or 0))

        rows.append(
            {
                "league_id": row.get("league_id"),
                "season": season,
                "owner_id": owner_id_by_name.get(owner_name),
                "owner_name": owner_name,
                "display_name": display_name_by_name.get(owner_name, owner_name),
                "sleeper_id": sleeper_id,
                "player_name": row.get("player"),
                "pos": row.get("pos"),
                "status": "active",
                "salary": salary,
                "years": years_left,
                "years_left": years_left,
                "contract_total_years": total_years,
                "is_rookie": bool(row.get("is_rookie") or False),
                "engine_score": engine_score,
                "engine_tier": engine_tier,
                "engine_summary": engine_summary,
            }
        )

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.drop_duplicates(
            subset=["league_id", "season", "owner_name", "sleeper_id"],
            keep="first",
        ).reset_index(drop=True)

    return df
