from __future__ import annotations


def project_legacy_contracts(agreements: list[dict], schedules: list[dict], *, season: int) -> list[dict]:
    by_contract = {}
    for row in schedules:
        by_contract.setdefault(str(row.get("contract_id") or row.get("contract_key")), []).append(row)
    projected = []
    for contract in agreements:
        key = str(contract.get("id") or contract.get("client_key"))
        current = next((x for x in by_contract.get(key, []) if int(x["season"]) == season), None)
        if not current: continue
        projected.append({"id": contract.get("source_legacy_contract_id"), "league_id": contract["league_id"],
            "league_team_id": contract["league_team_id"], "sleeper_player_id": contract["sleeper_player_id"],
            "salary": current["salary"], "contract_years_left": int(contract["end_season"]) - season + 1,
            "status": contract["status"]})
    return sorted(projected, key=lambda x: str(x["id"]))
