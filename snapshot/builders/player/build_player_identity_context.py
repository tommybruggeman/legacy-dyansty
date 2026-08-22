from __future__ import annotations

from datetime import datetime, timezone
import re

import pandas as pd

from auth import service_client


def normalize_name(name: str) -> str:
    name = str(name or "").strip().lower()
    name = re.sub(r"[^a-z\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def short_name(name: str) -> str:
    parts = str(name or "").strip().split()
    if len(parts) < 2:
        return str(name or "").strip()
    return f"{parts[0][0]}.{parts[-1]}"


def first_existing(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def normalize_source_rows(rows: list[dict], source: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    name_col = first_existing(df, ["player_name", "player", "name", "full_name"])
    pos_col = first_existing(df, ["pos", "position"])
    team_col = first_existing(df, ["nfl_team", "team"])
    sleeper_col = first_existing(df, ["sleeper_id", "sleeper_player_id", "player_id"])

    if name_col is None:
        print(f"Skipping {source}: no name column found. Columns={df.columns.tolist()}")
        return pd.DataFrame()

    out = pd.DataFrame()
    out["player_name"] = df[name_col].astype(str).str.strip()
    out["position"] = df[pos_col] if pos_col else None
    out["nfl_team"] = df[team_col] if team_col else None
    out["sleeper_id"] = df[sleeper_col] if sleeper_col else None
    out["source"] = source

    return out


def build_identity_rows() -> list[dict]:
    sb = service_client()
    now = datetime.now(timezone.utc).isoformat()

    sources = []

    for table in ["roster", "player_rankings", "player_identity_map"]:
        try:
            rows = sb.table(table).select("*").limit(10000).execute().data or []
            df = normalize_source_rows(rows, table)
            if not df.empty:
                sources.append(df)
                print(f"Loaded identity source {table}: {len(df)} rows")
        except Exception as e:
            print(f"Skipping {table}: {e}")

    if not sources:
        return []

    df = pd.concat(sources, ignore_index=True)

    df["player_name"] = df["player_name"].astype(str).str.strip()
    df = df[df["player_name"] != ""].copy()
    df = df[df["player_name"].str.lower() != "none"].copy()

    # Remove rows where an ID accidentally landed in the name field.
    df = df[~df["player_name"].str.match(r"^\d+$", na=False)].copy()

    df["normalized_name"] = df["player_name"].apply(normalize_name)
    df["nflverse_name"] = df["player_name"].apply(short_name)

    rows = []

    for normalized_name, g in df.groupby("normalized_name"):
        best = g.iloc[0]

        sleeper_id = None
        vals = g["sleeper_id"].dropna().astype(str)
        vals = vals[(vals != "") & (vals.str.lower() != "none")]
        if not vals.empty:
            sleeper_id = vals.iloc[0]

        canonical_player_id = sleeper_id or f"manual_{normalized_name.replace(' ', '_')}"

        position = None
        vals = g["position"].dropna().astype(str)
        vals = vals[(vals != "") & (vals.str.lower() != "none")]
        if not vals.empty:
            position = vals.iloc[0]

        nfl_team = None
        vals = g["nfl_team"].dropna().astype(str)
        vals = vals[(vals != "") & (vals.str.lower() != "none")]
        if not vals.empty:
            nfl_team = vals.iloc[0]

        rows.append(
            {
                "canonical_player_id": str(canonical_player_id),
                "player_name": str(best["player_name"]).strip(),
                "normalized_name": normalized_name,
                "sleeper_id": sleeper_id,
                "nflverse_name": str(best["nflverse_name"]),
                "nfl_team": nfl_team,
                "position": position,
                "source": "identity_builder_v2",
                "confidence_score": 90 if sleeper_id else 70,
                "created_at": now,
            }
        )

    return rows


def main() -> None:
    sb = service_client()
    rows = build_identity_rows()

    print(f"Built identity rows: {len(rows)}")

    if rows:
        deduped = {}
        for row in rows:
            key = str(row.get("canonical_player_id")).strip()

            existing = deduped.get(key)
            if existing is None or float(row.get("confidence_score") or 0) > float(existing.get("confidence_score") or 0):
                deduped[key] = row

        rows = list(deduped.values())
        print(f"Deduped identity rows: {len(rows)}")

        sb.table("player_identity_context").upsert(
            rows,
            on_conflict="canonical_player_id",
        ).execute()

    print(f"Upserted {len(rows)} identity rows.")


if __name__ == "__main__":
    main()
