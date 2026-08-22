from __future__ import annotations

from auth import service_client


ROOKIE_CLASS_YEAR = 2026


def norm(s: str) -> str:
    return " ".join((s or "").lower().replace(".", "").split())


def tag_rookie_class() -> None:
    sb = service_client()

    registry = (
        sb.table("rookie_class_registry")
        .select("player_name,pos,sleeper_id,rookie_class_year,is_active")
        .eq("rookie_class_year", ROOKIE_CLASS_YEAR)
        .eq("is_active", True)
        .execute()
        .data
        or []
    )

    if not registry:
        print(f"No explicit rookie_class_registry rows found for {ROOKIE_CLASS_YEAR}. Nothing tagged.")
        return

    allowed = {(norm(r["player_name"]), r["pos"]): r for r in registry}

    players = (
        sb.table("player_universe")
        .select("sleeper_id,player_name,pos")
        .in_("pos", ["QB", "RB", "WR", "TE"])
        .execute()
        .data
        or []
    )

    updates = []

    for p in players:
        key = (norm(p.get("player_name")), p.get("pos"))
        if key not in allowed:
            continue

        updates.append({
            "sleeper_id": p["sleeper_id"],
            "rookie_class_year": ROOKIE_CLASS_YEAR,
        })

    if updates:
        sb.table("player_universe").upsert(
            updates,
            on_conflict="sleeper_id",
        ).execute()

    print(f"Tagged {len(updates)} registry-approved rookies for class {ROOKIE_CLASS_YEAR}")


if __name__ == "__main__":
    tag_rookie_class()
