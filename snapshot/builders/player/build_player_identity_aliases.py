from __future__ import annotations

from datetime import datetime, timezone
import re

import pandas as pd

from auth import service_client


def normalize_alias(value: str) -> str:
    value = str(value or "").strip().lower()
    value = value.replace("'", "")
    value = value.replace(".", "")
    value = value.replace("-", " ")
    value = re.sub(r"[^a-z0-9\s]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def alias_variants(name: str) -> set[str]:
    raw = str(name or "").strip()
    clean = raw.replace("'", "").replace(".", "")
    parts = clean.split()

    aliases = {raw, clean}

    if len(parts) >= 2:
        first = parts[0]
        last = parts[-1]

        aliases.add(f"{first[0]}.{last}")
        aliases.add(f"{first[0]} {last}")

        # Handles Amon-Ra St. Brown → A.St. Brown
        if len(parts) >= 3:
            last_two = " ".join(parts[-2:])
            aliases.add(f"{first[0]}.{last_two}")
            aliases.add(f"{first[0]} {last_two}")

        # Handles full no-punctuation version
        aliases.add(" ".join(parts))

    return {a.strip() for a in aliases if a and a.strip()}


def main() -> None:
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    identities = (
        sb.table("player_identity_context")
        .select("canonical_player_id,player_name,nflverse_name,sleeper_id")
        .execute()
        .data
        or []
    )

    rows = []

    for ident in identities:
        canonical_id = ident["canonical_player_id"]
        player_name = ident["player_name"]

        for alias in alias_variants(player_name):
            rows.append({
                "canonical_player_id": canonical_id,
                "alias": alias,
                "normalized_alias": normalize_alias(alias),
                "alias_type": "generated",
                "source": "identity_alias_builder_v1",
                "confidence_score": 80,
                "created_at": now,
            })

        nflverse_name = ident.get("nflverse_name")
        if nflverse_name:
            rows.append({
                "canonical_player_id": canonical_id,
                "alias": nflverse_name,
                "normalized_alias": normalize_alias(nflverse_name),
                "alias_type": "nflverse",
                "source": "identity_alias_builder_v1",
                "confidence_score": 95,
                "created_at": now,
            })

        sleeper_id = ident.get("sleeper_id")
        if sleeper_id:
            rows.append({
                "canonical_player_id": canonical_id,
                "alias": str(sleeper_id),
                "normalized_alias": normalize_alias(str(sleeper_id)),
                "alias_type": "sleeper_id",
                "source": "identity_alias_builder_v1",
                "confidence_score": 100,
                "created_at": now,
            })

    deduped = {}
    for row in rows:
        key = (row["canonical_player_id"], row["normalized_alias"])
        existing = deduped.get(key)
        if existing is None or row["confidence_score"] > existing["confidence_score"]:
            deduped[key] = row

    rows = list(deduped.values())

    print(f"Built identity aliases: {len(rows)}")

    if rows:
        sb.table("player_identity_aliases").upsert(
            rows,
            on_conflict="canonical_player_id,normalized_alias",
        ).execute()

    print(f"Upserted identity aliases: {len(rows)}")


if __name__ == "__main__":
    main()
