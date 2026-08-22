from __future__ import annotations

from collections import defaultdict
from pprint import pprint

from auth import service_client
from gm_assistant.identity.resolver import (
    IdentityCandidate,
    canonical_key,
    normalize_name,
    resolve_cluster,
)


TABLES = [
    "player_graph_v2",
    "player_graph",
    "player_universe",
    "player_season_stats",
    "player_weekly_stats",
    "player_career_features",
    "player_value_engine",
]


def _get_nested(row: dict, key: str):
    return (row.get("identity") or {}).get(key)


def load_candidates(limit: int = 25000) -> list[IdentityCandidate]:
    sb = service_client()
    candidates: list[IdentityCandidate] = []

    for table in TABLES:
        try:
            rows = sb.table(table).select("*").limit(limit).execute().data or []
        except Exception as e:
            print(f"{table}: ERROR {e}")
            continue

        print(f"{table}: {len(rows)} rows")

        for r in rows:
            sleeper = r.get("sleeper_id") or _get_nested(r, "sleeper_id")
            gsis = r.get("gsis_id") or _get_nested(r, "gsis_id")

            candidates.append(
                IdentityCandidate(
                    table=table,
                    player_name=r.get("player_name"),
                    pos=r.get("pos"),
                    sleeper_id=str(sleeper) if sleeper else None,
                    gsis_id=str(gsis) if gsis else None,
                    owner=r.get("owner_team_name") or r.get("current_owner"),
                    canonical_player_id=r.get("canonical_player_id"),
                    data=r,
                )
            )

    return candidates


def build_clusters(candidates: list[IdentityCandidate]):
    from gm_assistant.identity.graph import build_identity_graph

    result = build_identity_graph(candidates)

    print("\nIDENTITY GRAPH")
    print("nodes:", result.node_count)
    print("edges:", result.edge_count)
    print("components:", result.component_count)

    return result.clusters


def main():
    candidates = load_candidates()
    clusters = build_clusters(candidates)

    print("\n" + "=" * 100)
    print("CLUSTERS:", len(clusters))

    warnings = [c for c in clusters if c.warnings]
    print("WARNING CLUSTERS:", len(warnings))

    print("\nTOP WARNING CLUSTERS")
    for c in warnings[:50]:
        print("\n", c.to_row())
        for cand in c.candidates[:8]:
            print(
                " -",
                cand.table,
                cand.player_name,
                cand.pos,
                cand.sleeper_id,
                cand.gsis_id,
                cand.owner,
                cand.canonical_player_id,
            )

    print("\n" + "=" * 100)
    print("MCNICHOLS")
    for c in clusters:
        if "mcnichols" in " ".join(c.aliases):
            pprint(c.to_row())
            for cand in c.candidates:
                print(
                    " -",
                    cand.table,
                    cand.player_name,
                    cand.pos,
                    cand.sleeper_id,
                    cand.gsis_id,
                    cand.owner,
                    cand.canonical_player_id,
                )


if __name__ == "__main__":
    main()
