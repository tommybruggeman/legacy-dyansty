from __future__ import annotations

from collections import defaultdict
from pprint import pprint

from auth import service_client


TABLES = [
    "player_graph_v2",
    "player_graph",
    "player_universe",
    "player_season_stats",
    "player_weekly_stats",
    "player_career_features",
    "player_value_engine",
]


def norm(v):
    return str(v or "").strip().lower()


def main():
    sb = service_client()

    by_sleeper = defaultdict(list)
    by_gsis = defaultdict(list)
    by_name = defaultdict(list)

    for table in TABLES:
        try:
            rows = sb.table(table).select("*").limit(10000).execute().data or []
        except Exception as e:
            print(f"{table}: ERROR {e}")
            continue

        print(f"{table}: {len(rows)} rows")

        for r in rows:
            name = r.get("player_name")
            pos = r.get("pos")
            sleeper = r.get("sleeper_id") or (r.get("identity") or {}).get("sleeper_id")
            gsis = r.get("gsis_id") or (r.get("identity") or {}).get("gsis_id")
            canonical = r.get("canonical_player_id")

            item = {
                "table": table,
                "player_name": name,
                "pos": pos,
                "sleeper_id": sleeper,
                "gsis_id": gsis,
                "canonical_player_id": canonical,
                "owner": r.get("owner_team_name") or r.get("current_owner"),
                "season_ppg": r.get("season_ppg") or r.get("fantasy_ppg_ppr"),
                "expected_ppg": r.get("expected_ppg"),
                "source": (r.get("production") or {}).get("source") or r.get("source"),
            }

            if sleeper:
                by_sleeper[norm(sleeper)].append(item)
            if gsis:
                by_gsis[norm(gsis)].append(item)
            if name:
                by_name[norm(name)].append(item)

    print("\n" + "=" * 100)
    print("IDENTITY FRAGMENTS BY SLEEPER ID")
    for sleeper, items in by_sleeper.items():
        poses = {i.get("pos") for i in items if i.get("pos")}
        names = {i.get("player_name") for i in items if i.get("player_name")}
        canonicals = {i.get("canonical_player_id") for i in items if i.get("canonical_player_id")}
        if len(poses) > 1 or len(canonicals) > 1:
            print("\nSLEEPER:", sleeper, "NAMES:", names, "POS:", poses, "CANONICALS:", canonicals)
            pprint(items[:12])

    print("\n" + "=" * 100)
    print("IDENTITY FRAGMENTS BY GSIS ID")
    for gsis, items in by_gsis.items():
        poses = {i.get("pos") for i in items if i.get("pos")}
        names = {i.get("player_name") for i in items if i.get("player_name")}
        canonicals = {i.get("canonical_player_id") for i in items if i.get("canonical_player_id")}
        if len(poses) > 1 or len(canonicals) > 1:
            print("\nGSIS:", gsis, "NAMES:", names, "POS:", poses, "CANONICALS:", canonicals)
            pprint(items[:12])

    print("\n" + "=" * 100)
    print("JEREMY MCNICHOLS TRACE")
    for key, items in by_name.items():
        if "mcnichols" in key:
            pprint(items)


if __name__ == "__main__":
    main()
