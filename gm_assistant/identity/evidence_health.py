from __future__ import annotations

from auth import service_client


def _fetch(table: str, player_name: str):
    sb = service_client()
    try:
        return (
            sb.table(table)
            .select("*")
            .ilike("player_name", f"%{player_name}%")
            .limit(20)
            .execute()
            .data
            or []
        )
    except Exception as e:
        return [{"_error": str(e)}]


def player_evidence_health(player_name: str) -> dict:
    tables = {
        "graph_v2": "player_graph_v2",
        "graph": "player_graph",
        "universe": "player_universe",
        "weekly": "player_weekly_stats",
        "season": "player_season_stats",
        "career": "player_career_features",
        "value": "player_value_engine",
    }

    result = {"player_name": player_name, "sources": {}, "warnings": []}

    for label, table in tables.items():
        rows = _fetch(table, player_name)
        result["sources"][label] = {
            "table": table,
            "rows": len([r for r in rows if not r.get("_error")]),
            "error": rows[0].get("_error") if rows and rows[0].get("_error") else None,
            "positions": sorted({str(r.get("pos")) for r in rows if r.get("pos")}),
            "sleeper_ids": sorted({str(r.get("sleeper_id")) for r in rows if r.get("sleeper_id")}),
            "gsis_ids": sorted({str(r.get("gsis_id")) for r in rows if r.get("gsis_id")}),
        }

    if result["sources"]["career"]["rows"] and not result["sources"]["weekly"]["rows"]:
        result["warnings"].append("career_exists_but_weekly_missing")

    if result["sources"]["value"]["rows"] and not result["sources"]["graph_v2"]["rows"]:
        result["warnings"].append("value_exists_but_graph_missing")

    all_positions = set()
    for s in result["sources"].values():
        all_positions.update(s["positions"])
    if len(all_positions) > 1:
        result["warnings"].append(f"position_conflict:{sorted(all_positions)}")

    return result
