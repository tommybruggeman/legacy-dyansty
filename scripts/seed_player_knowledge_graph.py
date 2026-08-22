from __future__ import annotations

import re
import unicodedata

from auth import service_client


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


PLAYERS = [
    {
        "player_name": "Omarion Hampton",
        "pos": "RB",
        "nfl_team": "LAC",
        "draft_class": 2025,
        "draft_round": 1,
        "draft_pick": 22,
        "college": "North Carolina",
        "player_stage": "rookie",
        "archetype": "premium rookie rb",
        "age_score": 90,
        "draft_capital_score": 88,
        "college_production_score": 82,
        "landing_spot_score": 82,
        "role_opportunity_score": 78,
        "market_value_score": 74,
        "notes": "Premium rookie RB with strong draft capital, college production, and Chargers opportunity.",
    },
    {
        "player_name": "Matthew Golden",
        "pos": "WR",
        "draft_class": 2025,
        "player_stage": "rookie",
        "archetype": "rookie wr upside",
        "age_score": 88,
        "draft_capital_score": 76,
        "college_production_score": 68,
        "landing_spot_score": 65,
        "role_opportunity_score": 62,
        "market_value_score": 60,
        "notes": "Rookie WR upside profile; needs role and production confirmation.",
    },
]


def main():
    sb = service_client()

    rows = []
    for p in PLAYERS:
        row = dict(p)
        row["normalized_name"] = normalize(p["player_name"])
        row["source"] = "manual_seed"
        rows.append(row)

    sb.table("player_knowledge_graph").upsert(
        rows,
        on_conflict="normalized_name",
    ).execute()

    print(f"Upserted {len(rows)} knowledge graph rows.")


if __name__ == "__main__":
    main()
