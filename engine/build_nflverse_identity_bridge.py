from __future__ import annotations

# ============================================================
# Imports / Path Setup
# ============================================================

import sys
from pathlib import Path
from difflib import SequenceMatcher

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth import service_client


# ============================================================
# Config
# ============================================================

NFLVERSE_PLAYERS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
)

SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"


# ============================================================
# Helpers
# ============================================================

def norm_name(x: str) -> str:
    return (
        str(x or "")
        .lower()
        .replace(".", "")
        .replace("'", "")
        .replace("-", " ")
        .replace(" iii", "")
        .replace(" ii", "")
        .replace(" jr", "")
        .replace(" sr", "")
        .strip()
    )


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_name(a), norm_name(b)).ratio()


def clean_sleeper_name(player: dict) -> str | None:
    return (
        player.get("full_name")
        or player.get("search_full_name")
        or player.get("last_name")
    )


# ============================================================
# Main Bridge Builder
# ============================================================

def main():
    sb = service_client()

    print("Loading nflverse players...")
    nfl = pd.read_csv(NFLVERSE_PLAYERS_URL, low_memory=False)

    print("Loading Sleeper players...")
    sleeper_players = requests.get(SLEEPER_PLAYERS_URL, timeout=30).json()

    print("Loading local identity map...")
    local = (
        sb.table("player_identity_map")
        .select("canonical_player_id,sleeper_id,player_name,pos")
        .execute()
        .data
    )

    local_df = pd.DataFrame(local)

    if local_df.empty:
        raise RuntimeError("player_identity_map is empty.")

    rows = []

    for _, lp in local_df.iterrows():

        # ====================================================
        # Local/Sleeper Identity
        # ====================================================

        sleeper_id = str(lp.get("sleeper_id") or "").strip()

        if not sleeper_id:
            continue

        sleeper_player = sleeper_players.get(sleeper_id)

        if not sleeper_player:
            continue

        local_name = clean_sleeper_name(sleeper_player) or lp.get("player_name")
        local_pos = sleeper_player.get("position") or lp.get("pos")
        local_team = sleeper_player.get("team")
        canonical_id = lp.get("canonical_player_id")

        if not local_name:
            continue

        # ====================================================
        # Candidate Matching
        # ====================================================

        candidates = nfl.copy()

        if local_pos and "position" in candidates.columns:
            candidates = candidates[
                candidates["position"].astype(str).str.upper()
                == str(local_pos).upper()
            ]

        candidates["score"] = candidates["display_name"].apply(
            lambda x: similarity(local_name, x)
        )

        best = candidates.sort_values("score", ascending=False).head(1)

        if best.empty:
            continue

        bp = best.iloc[0]
        confidence = float(bp["score"])

        # ====================================================
        # Match Method
        # ====================================================

        if confidence >= 0.97:
            method = "sleeper_name_pos_exact"
        elif confidence >= 0.90:
            method = "sleeper_name_pos_fuzzy"
        else:
            method = "low_confidence_review"

        # ====================================================
        # Bridge Row
        # ====================================================

        rows.append({
            "canonical_player_id": canonical_id,
            "sleeper_id": sleeper_id,
            "nflverse_id": bp.get("nflverse_id"),
            "gsis_id": bp.get("gsis_id"),
            "pfr_id": bp.get("pfr_id"),
            "sportradar_id": bp.get("sportradar_id"),
            "espn_id": (
                str(bp.get("espn_id"))
                if pd.notna(bp.get("espn_id"))
                else None
            ),
            "player_name": local_name,
            "first_name": bp.get("first_name"),
            "last_name": bp.get("last_name"),
            "pos": local_pos,
            "team": local_team or bp.get("latest_team"),
            "match_method": method,
            "match_confidence": confidence,
            "source": "sleeper_nflverse",
        })

    # ========================================================
    # Upsert Results
    # ========================================================

    print(f"Matched rows: {len(rows)}")

    if rows:
        sb.table("player_identity_bridge").upsert(
            rows,
            on_conflict="sleeper_id"
        ).execute()

    # ========================================================
    # Review Output
    # ========================================================

    review = [
        r for r in rows
        if r["match_method"] == "low_confidence_review"
    ]

    print(f"Upserted {len(rows)} bridge rows.")
    print(f"Needs review: {len(review)}")

    if review:
        print(pd.DataFrame(review)[[
            "player_name",
            "pos",
            "team",
            "match_confidence",
            "match_method",
            "sleeper_id",
            "gsis_id",
            "nflverse_id",
        ]].head(30))


# ============================================================
# Entrypoint
# ============================================================

if __name__ == "__main__":
    main()