from __future__ import annotations

from collections import defaultdict

from auth import service_client


def score_row(r: dict) -> tuple:
    return (
        1 if r.get("is_rostered") else 0,
        1 if r.get("owner_team_name") else 0,
        1 if r.get("salary") and float(r.get("salary") or 0) > 0 else 0,
        1 if r.get("is_active") else 0,
        -1 if r.get("is_free_agent") else 0,
        float(r.get("data_confidence") or 0),
    )


def dedupe_player_graph_v2():
    sb = service_client()

    rows = (
        sb.table("player_graph_v2")
        .select("*")
        .execute()
        .data
        or []
    )

    by_sid = defaultdict(list)
    for r in rows:
        sid = str(r.get("sleeper_id") or "")
        if sid:
            by_sid[sid].append(r)

    deleted = 0
    duplicate_groups = 0

    for sid, group in by_sid.items():
        if len(group) <= 1:
            continue

        duplicate_groups += 1
        keep = sorted(group, key=score_row, reverse=True)[0]

        for r in group:
            if r is keep:
                continue

            sb.table("player_graph_v2").delete() \
                .eq("sleeper_id", sid) \
                .eq("canonical_player_id", r.get("canonical_player_id")) \
                .execute()

            deleted += 1

    print(f"Duplicate sleeper_id groups: {duplicate_groups}")
    print(f"Deleted duplicate graph rows: {deleted}")


if __name__ == "__main__":
    dedupe_player_graph_v2()
