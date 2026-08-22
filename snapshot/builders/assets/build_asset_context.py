from __future__ import annotations

import pandas as pd

from gm_assistant.assets.classifier import AssetClassifier

classifier = AssetClassifier()


from gm_assistant.assets.classifier import AssetClassifier

classifier = AssetClassifier()


from auth import service_client

TARGET_TABLE = "asset_context"


def career_stage(exp: int | None) -> str:

    if exp is None:
        return "UNKNOWN"

    if exp <= 0:
        return "YEAR_0"

    if exp == 1:
        return "YEAR_1"

    if exp == 2:
        return "YEAR_2"

    if exp <= 5:
        return "PRIME"

    return "VETERAN"


def build_asset_context():

    sb = service_client()

    players = (
        sb.table("player_universe")
        .select("*")
        .execute()
        .data
        or []
    )

    from snapshot.context.season_context import season_context

    current_season = season_context.current_season()

    rows = []

    for p in players:

        rookie_year = p.get("rookie_class_year")

        exp = None

        if rookie_year:
            exp = current_season - rookie_year

        asset = classifier.classify(p)

        rows.append({

            "sleeper_id": p.get("sleeper_id"),

            "player_name": p.get("player_name"),

            "asset_type": "PLAYER",

            "rookie_class_year": rookie_year,

            "nfl_experience": exp,

            "career_stage": career_stage(exp),

            "is_rookie": exp == 0 if exp is not None else False,

            "is_free_agent": p.get("current_owner") is None,

            "current_owner": p.get("current_owner"),

            "position": p.get("pos"),

            "asset_category": asset["asset_category"],
            "asset_subtype": asset["asset_subtype"],
            "tradeable": asset["tradeable"],
            "rostered": asset["rostered"],
            "active_player": asset["active_player"],
            "market_pool": asset["market_pool"],

        })

    df = pd.DataFrame(rows)

    # Replace pandas NaN with Python None
    df = df.astype(object).where(pd.notnull(df), None)

    int_cols = [
        "rookie_class_year",
        "nfl_experience",
    ]

    for col in int_cols:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: int(x) if x is not None else None
            )

    print(df.head())

    records = []

    for r in df.to_dict("records"):

        clean = {}

        for k, v in r.items():

            if pd.isna(v):
                clean[k] = None
            elif k in {"rookie_class_year", "nfl_experience"}:
                clean[k] = int(v) if v is not None else None
            else:
                clean[k] = v

        records.append(clean)

    sb.table(TARGET_TABLE).upsert(
        records,
        on_conflict="sleeper_id",
    ).execute()

    print(f"Upserted {len(df)} asset_context rows")


if __name__ == "__main__":
    build_asset_context()
