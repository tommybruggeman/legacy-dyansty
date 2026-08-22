from auth import service_client
from services.sleeper_sync_guard import require_active_season_sync_authority


def _key(v):
    return str(v).strip() if v is not None else None


def _first(*vals):
    for v in vals:
        if v is not None and str(v).strip() != "":
            return v
    return None


def rebuild_roster_state(*, league_id: str, expected_season: int, sleeper_league_id: str):
    sb = service_client()
    require_active_season_sync_authority(sb, league_id=league_id, expected_season=expected_season,
                                         sleeper_league_id=sleeper_league_id)

    print("\n==============================")
    print("GM ROSTER REBUILD (ENRICHED)")
    print("==============================\n")

    # -------------------------------------------------
    # BASE ROSTER (Sleeper current state)
    # -------------------------------------------------
    base = sb.table("rosters_current").select("*").execute().data or []

    ownership = {}
    meta = {}

    for r in base:
        player_id = _key(r.get("player_id"))
        team_id = r.get("team_id")

        if not player_id:
            continue

        ownership[player_id] = team_id
        meta[player_id] = {
            "player_name": player_id,
            "position": None,
            "league_id": r.get("league_id"),
        }

    print(f"Base players: {len(ownership)}")

    # -------------------------------------------------
    # TRANSACTIONS (DELTA LAYER)
    # -------------------------------------------------
    txs = sb.table("transactions").select("*").execute().data or []
    txs.sort(key=lambda x: x.get("executed_at") or "")

    for tx in txs:
        player = tx.get("player")
        team = tx.get("team")
        tx_type = (tx.get("type") or "").upper()

        if not player:
            continue

        pid = _key(player)

        if tx_type in ["SIGN", "TRADE", "ADD", "WAIVER"]:
            if team:
                ownership[pid] = team

        elif tx_type in ["DROP", "CUT", "WAIVE"]:
            ownership.pop(pid, None)

        meta[pid] = {
            "player_name": player,
            "position": tx.get("pos"),
            "league_id": tx.get("league_id"),
        }

    print(f"After transactions: {len(ownership)}")

    # -------------------------------------------------
    # ENRICHMENT SOURCES
    # -------------------------------------------------
    contracts = sb.table("contracts").select("*").execute().data or []
    player_values = sb.table("player_values").select("*").execute().data or []
    player_rankings = sb.table("player_rankings").select("*").execute().data or []
    players = sb.table("players").select("*").execute().data or []
    sleeper_players = sb.table("sleeper_players").select("*").execute().data or []

    contract_map = {
        _key(c.get("sleeper_player_id")): c
        for c in contracts
        if _key(c.get("sleeper_player_id"))
    }

    value_map = {
        _key(v.get("player_id")): v
        for v in player_values
        if _key(v.get("player_id"))
    }

    ranking_map = {
        _key(r.get("sleeper_id")): r
        for r in player_rankings
        if _key(r.get("sleeper_id"))
    }

    players_map = {
        _key(p.get("sleeper_id")): p
        for p in players
        if _key(p.get("sleeper_id"))
    }

    sleeper_players_map = {
        _key(p.get("sleeper_player_id")): p
        for p in sleeper_players
        if _key(p.get("sleeper_player_id"))
    }

    print(f"Contracts loaded: {len(contracts)}")
    print(f"Contract player IDs mapped: {len(contract_map)}")
    print(f"Player values mapped: {len(value_map)}")
    print(f"Player rankings mapped: {len(ranking_map)}")
    print(f"Players mapped: {len(players_map)}")
    print(f"Sleeper players mapped: {len(sleeper_players_map)}")

    # -------------------------------------------------
    # CLEAR SNAPSHOT TABLE
    # -------------------------------------------------
    sb.table("team_roster_state").delete().neq("player_name", "__never__").execute()

    # -------------------------------------------------
    # REBUILD SNAPSHOT
    # -------------------------------------------------
    rows = []

    matched_contracts = 0
    enriched_names = 0
    enriched_positions = 0

    for pid, team in ownership.items():
        m = meta.get(pid, {})

        contract = contract_map.get(pid, {})
        value = value_map.get(pid, {})
        ranking = ranking_map.get(pid, {})
        player = players_map.get(pid, {})
        sleeper_player = sleeper_players_map.get(pid, {})

        if contract:
            matched_contracts += 1

        player_name = _first(
            contract.get("player_name"),
            value.get("player_name"),
            ranking.get("player"),
            player.get("full_name"),
            sleeper_player.get("full_name"),
            m.get("player_name"),
            pid,
        )

        position = _first(
            contract.get("player_position"),
            value.get("position"),
            ranking.get("pos"),
            player.get("position"),
            sleeper_player.get("position"),
            m.get("position"),
        )

        if player_name and player_name != pid:
            enriched_names += 1

        if position:
            enriched_positions += 1

        rows.append({
            "league_id": m.get("league_id"),
            "player_id": pid,
            "player_name": player_name,
            "team_id": team,
            "position": position,
            "status": "active",
            "salary": contract.get("salary"),
            "years": contract.get("contract_years_left"),
            "contract_status": "active" if contract else None,
        })

    if rows:
        sb.table("team_roster_state").insert(rows).execute()

    print(f"Matched contracts: {matched_contracts}")
    print(f"Enriched names: {enriched_names}")
    print(f"Enriched positions: {enriched_positions}")
    print(f"REBUILD COMPLETE → {len(rows)} players")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Rebuild the legacy roster mirror under canonical season authority.")
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--sleeper-league-id", required=True)
    args = parser.parse_args()
    rebuild_roster_state(league_id=args.league_id, expected_season=args.season,
                         sleeper_league_id=args.sleeper_league_id)
