from __future__ import annotations

from auth import service_client


TABLES = [
    "player_universe",
    "player_identity_context",
    "player_prospect_context",
    "prospect_graph",
    "player_situation_context",
    "player_role_context",
    "player_future_projection",
    "player_market_pool",
    "player_dynasty_context",
    "player_knowledge_graph",
    "player_engine_snapshot",
    "player_contract_roi",
]


def main():
    sb = service_client()

    print("\nPLAYER IDENTITY SOURCE AUDIT\n")

    for table in TABLES:
        try:
            rows = sb.table(table).select("*").limit(1).execute().data or []
            if not rows:
                print(f"\n{table}: exists but no rows")
                continue

            print(f"\n{table}")
            print("-" * len(table))
            for col in sorted(rows[0].keys()):
                print(col)

        except Exception as e:
            print(f"\n{table}: NOT AVAILABLE / ERROR")
            print(e)


if __name__ == "__main__":
    main()
