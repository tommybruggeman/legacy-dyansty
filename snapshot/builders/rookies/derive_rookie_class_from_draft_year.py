from __future__ import annotations

from auth import service_client
from snapshot.builders.rookies.rookie_year import get_active_rookie_class_year


ACTIVE_ROOKIE_CLASS_YEAR = get_active_rookie_class_year()


def derive_rookie_class_from_draft_year() -> None:
    sb = service_client()

    rows = (
        sb.table("player_universe")
        .select("sleeper_id,player_name,pos,draft_year,rookie_class_year")
        .in_("pos", ["QB", "RB", "WR", "TE"])
        .not_.is_("draft_year", "null")
        .execute()
        .data
        or []
    )

    updates = []

    for r in rows:
        draft_year = r.get("draft_year")
        if not draft_year:
            continue

        updates.append({
            "sleeper_id": r["sleeper_id"],
            "rookie_class_year": int(draft_year),
        })

    if updates:
        sb.table("player_universe").upsert(
            updates,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Derived rookie_class_year from draft_year for {len(updates)} players")


if __name__ == "__main__":
    derive_rookie_class_from_draft_year()
