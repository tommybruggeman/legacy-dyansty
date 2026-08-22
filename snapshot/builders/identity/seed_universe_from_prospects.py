from __future__ import annotations

from auth import service_client


ACTIVE_CLASS_YEAR = 2026


def norm(s: str) -> str:
    return " ".join((s or "").lower().replace(".", "").replace("'", "").split())


def load_sleeper_player_lookup(sb):
    all_rows = []
    page_size = 1000
    start = 0

    while True:
        rows = (
            sb.table("sleeper_players")
            .select("*")
            .range(start, start + page_size - 1)
            .execute()
            .data or []
        )

        all_rows.extend(rows)

        if len(rows) < page_size:
            break

        start += page_size

    by_name_pos = {}
    by_name = {}

    for r in all_rows:
        name = norm(r.get("search_name") or r.get("full_name"))
        pos = str(r.get("position") or "").upper()

        if name:
            by_name[name] = r

        if name and pos:
            by_name_pos[(name, pos)] = r

    return by_name_pos, by_name

def seed_universe_from_prospects() -> None:
    sb = service_client()

    existing = (
        sb.table("player_universe")
        .select("player_name,pos,search_name")
        .execute()
        .data or []
    )

    existing_keys = {
        (norm(r.get("player_name") or r.get("search_name")), r.get("pos"))
        for r in existing
    }

    prospects = (
        sb.table("player_prospect_context")
        .select("player_name,position,draft_year,college,draft_round,draft_pick,prospect_score,risk_notes")
        .eq("draft_year", ACTIVE_CLASS_YEAR)
        .execute()
        .data or []
    )

    sleeper_lookup, sleeper_name_lookup = load_sleeper_player_lookup(sb)

    rows = []

    for p in prospects:
        if "Auto-detected from Sleeper" in str(p.get("risk_notes") or ""):
            continue

        name = p.get("player_name")
        pos = p.get("position")

        if not name or pos not in {"QB", "RB", "WR", "TE"}:
            continue

        k = (norm(name), pos)
        if k in existing_keys:
            continue

        sleeper_match = sleeper_lookup.get((norm(name), pos)) or sleeper_name_lookup.get(norm(name))
        synthetic_id = (
            str(sleeper_match.get("sleeper_player_id"))
            if sleeper_match
            else f"prospect_{ACTIVE_CLASS_YEAR}_{norm(name).replace(' ', '_')}_{pos.lower()}"
        )
        nfl_team = sleeper_match.get("team") if sleeper_match else None

        rows.append({
            "sleeper_id": synthetic_id,
            "player_name": name,
            "search_name": norm(name),
            "pos": pos,
            "nfl_team": nfl_team,
            "market_pool": "ROOKIE_PROSPECT",
            "roster_status": "PROSPECT",
            "nfl_status": "PROSPECT",
            "active": False,
            "draft_year": int(p.get("draft_year")),
            "rookie_class_year": int(p.get("draft_year")),
            "draft_round": int(float(p.get("draft_round") or 0)) or None,
            "draft_pick": int(float(p.get("draft_pick") or 0)) or None,
            "college": p.get("college"),
            "rookie_asset_score": float(p.get("prospect_score") or 0),
            "player_universe_summary": "Seeded from prospect context as active rookie-class prospect.",
        })

    if rows:
        sb.table("player_universe").upsert(rows, on_conflict="sleeper_id").execute()

    print(f"Seeded {len(rows)} prospect-only players into player_universe")


if __name__ == "__main__":
    seed_universe_from_prospects()
