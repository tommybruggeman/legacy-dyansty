from __future__ import annotations

import pandas as pd

from snapshot.builders.player.build_players_snapshot import build_players_snapshot
from snapshot.loaders.contracts import load_contracts


def _clean_id(value):
    if value is None:
        return None
    return str(value).strip()


def _safe_row(row: pd.Series) -> dict:
    return row.where(pd.notnull(row), None).to_dict()


def build_rosters_snapshot(league_id: str | None = None) -> list[dict]:
    players = build_players_snapshot()
    contracts = load_contracts(league_id)

    if contracts.empty:
        contracts = load_contracts()

        if not contracts.empty:
            league_id = str(contracts["league_id"].value_counts().index[0])
            contracts = load_contracts(league_id)

    if not players or contracts.empty:
        return []

    players_by_id = {
        _clean_id(p.get("sleeper_id")): p
        for p in players
        if p.get("sleeper_id") is not None
    }

    snapshots = []

    for owner, group in contracts.groupby("owner"):
        enriched_players = []

        for _, row in group.iterrows():
            sleeper_id = _clean_id(row.get("sleeper_id"))
            player = players_by_id.get(sleeper_id, {}).copy()

            enriched_players.append(
                {
                    "sleeper_id": sleeper_id,
                    "player_name": row.get("player") or player.get("player_name"),
                    "pos": row.get("pos") or player.get("pos"),
                    "status": "active",
                    "contract": {
                        "salary": row.get("salary"),
                        "total_years": row.get("contract_total_years"),
                        "years_left": row.get("years"),
                    },
                    "engine": {
                        "score": player.get("engine_score"),
                        "tier": player.get("engine_tier"),
                        "summary": player.get("engine_summary"),
                    },
                    "player": player,
                    "contract_row": _safe_row(row),
                }
            )

        snapshots.append(
            {
                "owner_name": owner,
                "team_name": owner,
                "league_id": str(group["league_id"].iloc[0]),
                "roster_size": len(enriched_players),
                "players": enriched_players,
            }
        )

    return snapshots


if __name__ == "__main__":
    rosters = build_rosters_snapshot()

    print(f"Built roster snapshots: {len(rosters)} teams")

    if rosters:
        print("Sample team:", rosters[0].get("team_name"))
        print("Roster size:", rosters[0].get("roster_size"))
        print("Sample players:", rosters[0].get("players", [])[:3])
