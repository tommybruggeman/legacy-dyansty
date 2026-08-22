from __future__ import annotations

from datetime import datetime, timezone
import re

import pandas as pd

from auth import service_client


def normalize(v: str) -> str:
    v = str(v or "").strip().lower()
    v = v.replace("'", "")
    v = v.replace(".", "")
    v = v.replace("-", " ")
    v = re.sub(r"[^a-z0-9\s]", "", v)
    v = re.sub(r"\s+", " ", v)
    return v.strip()


def generate_alias(name: str) -> set[str]:
    name = str(name or "").strip()
    clean = name.replace("'", "").replace(".", "")
    parts = clean.split()

    aliases = set()

    if not parts:
        return aliases

    first = parts[0]
    last = parts[-1]

    # core nflverse style
    aliases.add(name)
    aliases.add(clean)

    aliases.add(f"{first[0]}.{last}")
    aliases.add(f"{first[0]} {last}")

    # multi-word last names
    if len(parts) >= 3:
        last_two = " ".join(parts[-2:])
        aliases.add(f"{first[0]}.{last_two}")
        aliases.add(f"{first[0]} {last_two}")

    return {normalize(a) for a in aliases if a}


def main():
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    # STEP 1: pull unresolved usage rows
    usage = (
        sb.table("player_usage_context")
        .select("player_name,nfl_team")
        .is_("canonical_player_id", "null")
        .execute()
        .data
        or []
    )

    df = pd.DataFrame(usage)

    if df.empty:
        print("No unresolved usage rows found.")
        return

    # STEP 2: existing alias table (to avoid duplicates)
    existing = (
        sb.table("player_identity_aliases")
        .select("normalized_alias")
        .execute()
        .data
        or []
    )

    existing_set = {r["normalized_alias"] for r in existing}

    rows = []

    # STEP 3: generate missing aliases from usage names
    for _, r in df.iterrows():
        name = r["player_name"]

        canonical_id = None

        # DO NOT block bootstrap
        from gm_assistant.tools.resolve_canonical_player import resolve_canonical_player
        canonical_id = resolve_canonical_player(name)

        for alias in generate_alias(name):
            if alias in existing_set:
                continue

            rows.append({
                "canonical_player_id": canonical_id or "unresolved",
                "alias": alias,
                "normalized_alias": alias,
                "alias_type": "usage_backfill",
                "source": "identity_backfill_v1",
                "confidence_score": 60,
                "created_at": now,
            })

    # STEP 4: dedupe
    dedup = {}
    for r in rows:
        dedup[r["normalized_alias"]] = r

    rows = list(dedup.values())

    print(f"Backfill aliases generated: {len(rows)}")

    if rows:
        sb.table("player_identity_aliases").upsert(
            rows,
            on_conflict="normalized_alias",
        ).execute()

    print(f"Upserted backfill aliases: {len(rows)}")


if __name__ == "__main__":
    main()
