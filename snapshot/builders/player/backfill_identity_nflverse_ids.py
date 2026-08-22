from __future__ import annotations

from auth import service_client


def norm(x):
    return str(x or "").strip()


def backfill_identity_nflverse_ids():
    sb = service_client()

    identity_rows = (
        sb.table("player_identity_map")
        .select("canonical_player_id,player_name,pos,nflverse_id")
        .execute()
        .data
        or []
    )

    print("Identity rows:", len(identity_rows))

    updated = 0
    unmatched = 0

    for p in identity_rows:
        if norm(p.get("nflverse_id")):
            continue

        name = norm(p.get("player_name"))
        pos = norm(p.get("pos"))
        canonical_id = norm(p.get("canonical_player_id"))

        if not name or not pos or not canonical_id:
            unmatched += 1
            continue

        matches = (
            sb.table("player_season_stats")
            .select("player_name,pos,sleeper_id,gsis_id,season")
            .eq("player_name", name)
            .eq("pos", pos)
            .order("season", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not matches:
            unmatched += 1
            continue

        m = matches[0]
        nflverse_id = norm(m.get("gsis_id")) or norm(m.get("sleeper_id"))

        if not nflverse_id:
            unmatched += 1
            continue

        sb.table("player_identity_map").update({
            "nflverse_id": nflverse_id,
        }).eq("canonical_player_id", canonical_id).execute()

        print(f"Updated {name} → {nflverse_id}")
        updated += 1

    print("Updated:", updated)
    print("Unmatched:", unmatched)


if __name__ == "__main__":
    backfill_identity_nflverse_ids()
