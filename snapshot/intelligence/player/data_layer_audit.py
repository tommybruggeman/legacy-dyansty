from __future__ import annotations

from auth import service_client


TABLES = {
    "identity": [
        "player_universe",
        "player_identity_context",
    ],
    "rookie": [
        "rookie_draft_board",
    ],
    "source_pipeline": [
        "legacy_source_task_queue",
    ],
    "team": [
        "team_future_context",
    ],
    "recommendations": [
        "player_recommendations",
    ],
    "contracts_or_assets": [
        "roster_asset_values",
    ],
}


def inspect_table(table: str) -> dict:
    sb = service_client()

    try:
        rows = sb.table(table).select("*").limit(3).execute().data or []
        columns = sorted(rows[0].keys()) if rows else []

        return {
            "table": table,
            "exists": True,
            "sample_count": len(rows),
            "columns": columns,
            "sample": rows[:1],
        }

    except Exception as e:
        return {
            "table": table,
            "exists": False,
            "error": str(e),
            "columns": [],
            "sample": [],
        }


def run_data_layer_audit() -> dict:
    results = {}

    for layer, tables in TABLES.items():
        results[layer] = [inspect_table(t) for t in tables]

    return results


if __name__ == "__main__":
    audit = run_data_layer_audit()

    for layer, checks in audit.items():
        print("\n" + "=" * 100)
        print(layer.upper())
        print("=" * 100)

        for check in checks:
            print(f"\nTABLE: {check['table']}")
            print("EXISTS:", check["exists"])

            if check["exists"]:
                print("COLUMNS:", check["columns"])
                print("SAMPLE:", check["sample"])
            else:
                print("ERROR:", check["error"])
