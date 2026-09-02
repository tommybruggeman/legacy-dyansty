from __future__ import annotations

# ============================================================
# Engine Health Validator
#
# Read-only diagnostic tool.
#
# Checks:
# - contracts
# - players
# - player_identity_map
# - player_rankings
# - player_career_features
# - player_engine_scores
# ============================================================

import sys
from pathlib import Path
from difflib import get_close_matches

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth import service_client


# ============================================================
# Supabase Client
# ============================================================

sb = service_client()


# ============================================================
# Helpers
# ============================================================

def load_table(table: str, cols: str = "*") -> pd.DataFrame:
    rows = (
        sb.table(table)
        .select(cols)
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)


def clean_id(series):
    return (
        series
        .astype(str)
        .str.strip()
    )


def norm_name(value) -> str:
    return (
        str(value or "")
        .lower()
        .replace(".", "")
        .replace("'", "")
        .replace("-", " ")
        .replace(" jr", "")
        .replace(" sr", "")
        .replace(" ii", "")
        .replace(" iii", "")
        .strip()
    )


def pct(part: int, total: int) -> str:
    if total == 0:
        return "0.0%"

    return f"{(part / total * 100):.1f}%"


def print_section(title: str):
    print("")
    print("=" * 72)
    print(title)
    print("=" * 72)


def print_subsection(title: str):
    print("")
    print(title)
    print("-" * 72)


# ============================================================
# Main
# ============================================================

def main():
    print_section("Fantasy DB Engine Health Check")

    contracts = load_table(
        "contracts",
        "league_id,owner_name,sleeper_player_id,player_name,player_position"
    )

    players = load_table(
        "players",
        "sleeper_id,full_name,position"
    )

    identity = load_table(
        "player_identity_map",
        "sleeper_id,player_name,pos,source"
    )

    rankings = load_table(
        "player_rankings",
        "sleeper_id,player,pos,base_player_score"
    )

    career = load_table(
        "player_career_features",
        "sleeper_id,player_name,pos,career_ppg_ppr"
    )

    engine = load_table(
        "player_engine_scores",
        "sleeper_id,player_name,pos,engine_score,base_player_score,recent_production_score"
    )

    # --------------------------------------------------------
    # Table Counts
    # --------------------------------------------------------

    print_subsection("Table Counts")
    print(f"contracts:              {len(contracts)}")
    print(f"players:                {len(players)}")
    print(f"player_identity_map:    {len(identity)}")
    print(f"player_rankings:        {len(rankings)}")
    print(f"player_career_features: {len(career)}")
    print(f"player_engine_scores:   {len(engine)}")

    if contracts.empty:
        print("")
        print("No contract rows found. Stopping.")
        return

    # --------------------------------------------------------
    # Normalize IDs
    # --------------------------------------------------------

    contracts["sleeper_player_id"] = clean_id(
        contracts["sleeper_player_id"]
    )

    for df in [players, identity, rankings, career, engine]:
        if not df.empty and "sleeper_id" in df.columns:
            df["sleeper_id"] = clean_id(df["sleeper_id"])

    # --------------------------------------------------------
    # Roster
    # --------------------------------------------------------

    roster = contracts[
        ~contracts["sleeper_player_id"]
        .astype(str)
        .str.startswith("manual_", na=False)
    ].copy()

    manual = contracts[
        contracts["sleeper_player_id"]
        .astype(str)
        .str.startswith("manual_", na=False)
    ].copy()

    roster = roster.dropna(
        subset=["sleeper_player_id"]
    ).copy()

    roster = roster.drop_duplicates(
        subset=["sleeper_player_id"]
    ).copy()

    # --------------------------------------------------------
    # Sets
    # --------------------------------------------------------

    player_ids = set(players["sleeper_id"]) if not players.empty else set()
    identity_ids = set(identity["sleeper_id"]) if not identity.empty else set()
    ranking_ids = set(rankings["sleeper_id"]) if not rankings.empty else set()
    career_ids = set(career["sleeper_id"]) if not career.empty else set()
    engine_ids = set(engine["sleeper_id"]) if not engine.empty else set()

    roster["in_players"] = roster["sleeper_player_id"].isin(player_ids)
    roster["in_identity_map"] = roster["sleeper_player_id"].isin(identity_ids)
    roster["in_rankings"] = roster["sleeper_player_id"].isin(ranking_ids)
    roster["in_career_features"] = roster["sleeper_player_id"].isin(career_ids)
    roster["in_engine_scores"] = roster["sleeper_player_id"].isin(engine_ids)

    # --------------------------------------------------------
    # Coverage
    # --------------------------------------------------------

    total = len(roster)

    print_subsection("Roster Coverage")
    print(f"unique real roster players: {total}")
    print(f"manual ID rows:             {len(manual)}")
    print("")
    print(
        f"players:                {roster['in_players'].sum()} / {total} "
        f"({pct(int(roster['in_players'].sum()), total)})"
    )
    print(
        f"identity_map:           {roster['in_identity_map'].sum()} / {total} "
        f"({pct(int(roster['in_identity_map'].sum()), total)})"
    )
    print(
        f"rankings:               {roster['in_rankings'].sum()} / {total} "
        f"({pct(int(roster['in_rankings'].sum()), total)})"
    )
    print(
        f"career_features:        {roster['in_career_features'].sum()} / {total} "
        f"({pct(int(roster['in_career_features'].sum()), total)})"
    )
    print(
        f"engine_scores:          {roster['in_engine_scores'].sum()} / {total} "
        f"({pct(int(roster['in_engine_scores'].sum()), total)})"
    )

    # --------------------------------------------------------
    # Missing Engine Scores
    # --------------------------------------------------------

    missing_engine = roster[
        ~roster["in_engine_scores"]
    ].copy()

    print_subsection("Missing Engine Scores")

    if missing_engine.empty:
        print("All roster players have engine scores.")
    else:
        print(
            missing_engine[
                [
                    "owner_name",
                    "player_name",
                    "player_position",
                    "sleeper_player_id",
                    "in_players",
                    "in_identity_map",
                    "in_rankings",
                    "in_career_features",
                ]
            ]
            .sort_values(["owner_name", "player_name"])
            .to_string(index=False)
        )

    # --------------------------------------------------------
    # Missing Rankings
    # --------------------------------------------------------

    missing_rankings = roster[
        ~roster["in_rankings"]
    ].copy()

    print_subsection("Missing Rankings")

    if missing_rankings.empty:
        print("All roster players have rankings.")
    else:
        print(
            missing_rankings[
                [
                    "owner_name",
                    "player_name",
                    "player_position",
                    "sleeper_player_id",
                    "in_players",
                    "in_identity_map",
                ]
            ]
            .sort_values(["owner_name", "player_name"])
            .to_string(index=False)
        )

    # --------------------------------------------------------
    # Manual IDs
    # --------------------------------------------------------

    print_subsection("Manual ID Rows")

    if manual.empty:
        print("No manual IDs found.")
    else:
        print(
            manual[
                [
                    "owner_name",
                    "player_name",
                    "player_position",
                    "sleeper_player_id",
                ]
            ]
            .sort_values(["owner_name", "player_name"])
            .to_string(index=False)
        )

    # --------------------------------------------------------
    # Possible Name Matches
    # --------------------------------------------------------

    print_subsection("Possible Name Matches For Missing Engine Scores")

    if missing_engine.empty or engine.empty:
        print("No missing engine rows to inspect.")
    else:
        engine_names = {
            norm_name(r["player_name"]): r
            for _, r in engine.iterrows()
            if "player_name" in engine.columns
        }

        engine_keys = list(engine_names.keys())

        suggestions = []

        for _, r in missing_engine.iterrows():
            key = norm_name(r.get("player_name"))

            matches = get_close_matches(
                key,
                engine_keys,
                n=1,
                cutoff=0.82,
            )

            if matches:
                match_key = matches[0]
                match = engine_names[match_key]

                suggestions.append(
                    {
                        "contract_player": r.get("player_name"),
                        "contract_pos": r.get("player_position"),
                        "contract_id": r.get("sleeper_player_id"),
                        "possible_engine_player": match.get("player_name"),
                        "engine_pos": match.get("pos"),
                        "engine_id": match.get("sleeper_id"),
                        "engine_score": match.get("engine_score"),
                    }
                )

        if not suggestions:
            print("No close name matches found.")
        else:
            print(
                pd.DataFrame(suggestions)
                .sort_values(["contract_player"])
                .to_string(index=False)
            )

    print("")
    print("Done.")


# ============================================================
# Entrypoint
# ============================================================

if __name__ == "__main__":
    main()