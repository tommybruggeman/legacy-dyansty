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


def build_aliases(name: str) -> set[str]:
    """
    Generate nflverse-style aliases:
    - Amon-Ra St. Brown → A.St. Brown / A Brown
    - James Conner → J.Conner
    - De'Von Achane → D.Achane
    """
    name = str(name or "").strip()
    clean = name.replace("'", "").replace(".", "")
    parts = clean.split()

    aliases = set()

    if not parts:
        return aliases

    first = parts[0]
    last = parts[-1]

    # core forms
    aliases.add(name)
    aliases.add(clean)

    # initial + last
    aliases.add(f"{first[0]}.{last}")
    aliases.add(f"{first[0]} {last}")

    # full last-name join forms
    aliases.add(f"{first[0]}.{''.join(parts[1:])}")
    aliases.add(f"{first[0]} {''.join(parts[1:])}")

    # multi-word last names (St Brown etc.)
    if len(parts) >= 3:
        last_two = " ".join(parts[-2:])
        aliases.add(f"{first[0]}.{last_two}")
        aliases.add(f"{first[0]} {last_two}")

    return {normalize(a) for a in aliases if a}


def main():
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    # pull nflverse usage names (source of truth for missing players)
    pbp = sb.table("player_usage_context") \
        .select("player_name") \
        .execute() \
        .data or []

    df = pd.DataFrame(pbp)

    if df.empty:
        print("No usage data found.")
        return

    rows = []

    for _, r in df.iterrows():
        name = r["player_name"]

        for alias in build_aliases(name):
            rows.append({
                "canonical_player_id": None,
                "alias": alias,
                "normalized_alias": normalize(alias),
                "alias_type": "nflverse_generated",
                "source": "nflverse_alias_extractor_v1",
                "confidence_score": 70,
                "created_at": now,
            })

    # dedupe
    deduped = {}
    for row in rows:
        key = (row["alias"], row["normalized_alias"])
        deduped[key] = row

    rows = list(deduped.values())

    print(f"Built nflverse aliases: {len(rows)}")

    if rows:
        sb.table("player_identity_aliases").upsert(
            rows,
            on_conflict="normalized_alias,alias",
        ).execute()

    print(f"Upserted nflverse aliases: {len(rows)}")


if __name__ == "__main__":
    main()
