from __future__ import annotations

# ============================================================
# Data Quality Layer
#
# Finds likely player name / identity issues.
#
# Read-only by default.
# Does not modify contracts unless we later add an apply step.
# ============================================================

import sys
from pathlib import Path
from difflib import SequenceMatcher

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


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(
        None,
        norm_name(a),
        norm_name(b),
    ).ratio()


def load_table(table: str, cols: str = "*") -> pd.DataFrame:
    rows = (
        sb.table(table)
        .select(cols)
        .execute()
        .data
        or []
    )

    return pd.DataFrame(rows)


# ============================================================
# Load Inputs
# ============================================================

def load_contract_players() -> pd.DataFrame:
    return load_table(
        "contracts",
        "id,league_id,owner_name,sleeper_player_id,player_name,player_position"
    )


def load_reference_players() -> pd.DataFrame:
    engine = load_table(
        "player_engine_scores",
        "sleeper_id,player_name,pos,engine_score"
    )

    identity = load_table(
        "player_identity_map",
        "sleeper_id,player_name,pos,source,confidence"
    )

    rankings = load_table(
        "player_rankings",
        "sleeper_id,player,pos,base_player_score"
    )

    refs = []

    if not engine.empty:
        e = engine.rename(
            columns={
                "player_name": "reference_name",
                "pos": "reference_pos",
            }
        )
        e["reference_source"] = "player_engine_scores"
        refs.append(
            e[
                [
                    "sleeper_id",
                    "reference_name",
                    "reference_pos",
                    "reference_source",
                ]
            ]
        )

    if not identity.empty:
        i = identity.rename(
            columns={
                "player_name": "reference_name",
                "pos": "reference_pos",
            }
        )
        i["reference_source"] = "player_identity_map"
        refs.append(
            i[
                [
                    "sleeper_id",
                    "reference_name",
                    "reference_pos",
                    "reference_source",
                ]
            ]
        )

    if not rankings.empty:
        r = rankings.rename(
            columns={
                "player": "reference_name",
                "pos": "reference_pos",
            }
        )
        r["reference_source"] = "player_rankings"
        refs.append(
            r[
                [
                    "sleeper_id",
                    "reference_name",
                    "reference_pos",
                    "reference_source",
                ]
            ]
        )

    if not refs:
        return pd.DataFrame()

    out = pd.concat(
        refs,
        ignore_index=True,
    )

    out = out.dropna(
        subset=["sleeper_id", "reference_name"]
    ).copy()

    out["sleeper_id"] = (
        out["sleeper_id"]
        .astype(str)
        .str.strip()
    )

    out["reference_pos"] = (
        out["reference_pos"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    out["name_key"] = out["reference_name"].apply(norm_name)

    return out.drop_duplicates(
        subset=["sleeper_id", "name_key"]
    )


# ============================================================
# Quality Checks
# ============================================================

def find_contract_name_issues(
    min_score: float = 0.82,
) -> pd.DataFrame:
    contracts = load_contract_players()
    refs = load_reference_players()

    if contracts.empty or refs.empty:
        return pd.DataFrame()

    df = contracts.copy()

    df["sleeper_player_id"] = (
        df["sleeper_player_id"]
        .astype(str)
        .str.strip()
    )

    df["player_position"] = (
        df["player_position"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    df = df[
        ~df["sleeper_player_id"]
        .str.startswith("manual_", na=False)
    ].copy()

    suggestions = []

    for _, r in df.iterrows():
        contract_id = str(r.get("sleeper_player_id"))
        contract_name = r.get("player_name")
        contract_pos = r.get("player_position")

        same_id = refs[
            refs["sleeper_id"].eq(contract_id)
        ].copy()

        if same_id.empty:
            candidates = refs[
                refs["reference_pos"].eq(contract_pos)
            ].copy()
        else:
            candidates = same_id

        if candidates.empty:
            continue

        candidates["match_score"] = candidates["reference_name"].apply(
            lambda x: similarity(contract_name, x)
        )

        best = (
            candidates
            .sort_values("match_score", ascending=False)
            .head(1)
        )

        if best.empty:
            continue

        b = best.iloc[0]

        contract_key = norm_name(contract_name)
        reference_key = norm_name(b["reference_name"])

        if contract_key == reference_key:
            continue

        if float(b["match_score"]) < min_score:
            continue

        suggestions.append(
            {
                "contract_row_id": r.get("id"),
                "league_id": r.get("league_id"),
                "owner_name": r.get("owner_name"),
                "current_name": contract_name,
                "current_pos": contract_pos,
                "current_sleeper_id": contract_id,
                "suggested_name": b.get("reference_name"),
                "suggested_pos": b.get("reference_pos"),
                "suggested_sleeper_id": b.get("sleeper_id"),
                "reference_source": b.get("reference_source"),
                "match_score": round(float(b["match_score"]), 3),
                "suggested_action": (
                    "rename_only"
                    if contract_id == str(b.get("sleeper_id"))
                    else "review_id_and_name"
                ),
            }
        )

    return pd.DataFrame(suggestions)


def find_unranked_roster_players() -> pd.DataFrame:
    contracts = load_contract_players()
    engine = load_table(
        "player_engine_scores",
        "sleeper_id,player_name,pos,engine_score"
    )

    if contracts.empty:
        return pd.DataFrame()

    df = contracts.copy()

    df["sleeper_player_id"] = (
        df["sleeper_player_id"]
        .astype(str)
        .str.strip()
    )

    df = df[
        ~df["sleeper_player_id"]
        .str.startswith("manual_", na=False)
    ].copy()

    engine_ids = set()

    if not engine.empty:
        engine["sleeper_id"] = (
            engine["sleeper_id"]
            .astype(str)
            .str.strip()
        )
        engine_ids = set(engine["sleeper_id"])

    missing = df[
        ~df["sleeper_player_id"].isin(engine_ids)
    ].copy()

    return missing[
        [
            "owner_name",
            "player_name",
            "player_position",
            "sleeper_player_id",
        ]
    ].sort_values(
        ["owner_name", "player_name"]
    )


# ============================================================
# Report
# ============================================================

def run_data_quality_report():
    print("")
    print("=" * 72)
    print("Fantasy DB Data Quality Report")
    print("=" * 72)

    name_issues = find_contract_name_issues()
    unranked = find_unranked_roster_players()

    print("")
    print("Likely Contract Name Issues")
    print("-" * 72)

    if name_issues.empty:
        print("No likely name issues found.")
    else:
        print(
            name_issues[
                [
                    "owner_name",
                    "current_name",
                    "current_pos",
                    "current_sleeper_id",
                    "suggested_name",
                    "suggested_sleeper_id",
                    "match_score",
                    "suggested_action",
                ]
            ].to_string(index=False)
        )

    print("")
    print("Roster Players Missing Engine Scores")
    print("-" * 72)

    if unranked.empty:
        print("All roster players have engine scores.")
    else:
        print(
            unranked.to_string(index=False)
        )

    print("")
    print("Done.")

    return {
        "name_issues": name_issues,
        "unranked_players": unranked,
    }


# ============================================================
# Entrypoint
# ============================================================

if __name__ == "__main__":
    run_data_quality_report()