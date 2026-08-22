from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from auth import service_client


SEASONS = [2024]


def _num(s, default=0):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def normalize_alias(value: str) -> str:
    import re
    value = str(value or "").strip().lower()
    value = value.replace("'", "")
    value = value.replace(".", "")
    value = value.replace("-", " ")
    value = re.sub(r"[^a-z0-9\s]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def load_pbp() -> pd.DataFrame:
    frames = []

    for season in SEASONS:
        url = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
        print(f"Loading nflverse PBP CSV: {url}")
        frames.append(pd.read_csv(url, compression="gzip", low_memory=False))

    return pd.concat(frames, ignore_index=True)


def infer_role_type(position, carries_pg, targets_pg, target_share):
    position = str(position or "").upper()

    if position == "RB":
        if carries_pg >= 12 and targets_pg >= 3:
            return "three-down RB"
        if carries_pg >= 12:
            return "early-down RB"
        if targets_pg >= 3:
            return "receiving RB"
        return "depth RB"

    if position == "WR":
        if target_share >= 24:
            return "alpha WR"
        if target_share >= 18:
            return "starting WR"
        if target_share >= 12:
            return "secondary WR"
        return "depth WR"

    if position == "TE":
        if target_share >= 18:
            return "featured TE"
        if target_share >= 10:
            return "starting TE"
        return "low-volume TE"

    if position == "QB":
        return "QB"

    return "unknown"


def build_usage_rows(pbp: pd.DataFrame) -> list[dict]:
    df = pbp.copy()

    df = df[df["posteam"].notna()].copy()
    df = df[df["play_type"].isin(["pass", "run"])].copy()

    df["is_red_zone"] = (_num(df.get("yardline_100")) <= 20).astype(int)

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    # Team pass attempts for target share denominator.
    team_passes = (
        df[df["play_type"] == "pass"]
        .groupby(["season", "posteam"])
        .size()
        .to_dict()
    )

    # Receiving usage
    rec = df[df["receiver_player_name"].notna()].copy()
    rec["target"] = 1
    rec["air_yards_num"] = _num(rec.get("air_yards"), 0)
    rec["red_zone_target"] = rec["is_red_zone"]

    rec_group = (
        rec.groupby(["season", "posteam", "receiver_player_name"], dropna=False)
        .agg(
            targets=("target", "sum"),
            air_yards=("air_yards_num", "sum"),
            red_zone_targets=("red_zone_target", "sum"),
            games=("week", "nunique"),
        )
        .reset_index()
        .rename(columns={"posteam": "nfl_team", "receiver_player_name": "player_name"})
    )

    # Rushing usage
    rush = df[df["rusher_player_name"].notna()].copy()
    rush["carry"] = 1
    rush["red_zone_carry"] = rush["is_red_zone"]

    rush_group = (
        rush.groupby(["season", "posteam", "rusher_player_name"], dropna=False)
        .agg(
            carries=("carry", "sum"),
            red_zone_carries=("red_zone_carry", "sum"),
            rush_games=("week", "nunique"),
        )
        .reset_index()
        .rename(columns={"posteam": "nfl_team", "rusher_player_name": "player_name"})
    )

    usage = pd.merge(
        rec_group,
        rush_group,
        on=["season", "nfl_team", "player_name"],
        how="outer",
    )

    for col in ["targets", "air_yards", "red_zone_targets", "carries", "red_zone_carries"]:
        usage[col] = _num(usage.get(col), 0)

    usage["games"] = usage[["games", "rush_games"]].max(axis=1).fillna(1).astype(int)

    # Identity bridge: any alias -> canonical player.
    sb = service_client()

    alias_rows = (
        sb.table("player_identity_aliases")
        .select("canonical_player_id,normalized_alias,confidence_score")
        .execute()
        .data
        or []
    )

    identity_rows = (
        sb.table("player_identity_context")
        .select("canonical_player_id,player_name,sleeper_id,position")
        .execute()
        .data
        or []
    )

    aliases = pd.DataFrame(alias_rows)
    identities = pd.DataFrame(identity_rows)

    identity_by_id = {}
    if not identities.empty:
        for _, ident in identities.iterrows():
            identity_by_id[str(ident["canonical_player_id"])] = {
                "canonical_player_id": ident.get("canonical_player_id"),
                "player_name": ident.get("player_name"),
                "sleeper_id": ident.get("sleeper_id"),
                "position": ident.get("position"),
            }

    identity_map = {}
    if not aliases.empty:
        aliases = aliases.sort_values("confidence_score", ascending=False)
        for _, alias in aliases.iterrows():
            key = str(alias["normalized_alias"])
            cid = str(alias["canonical_player_id"])
            if key not in identity_map and cid in identity_by_id:
                identity_map[key] = identity_by_id[cid]

    for _, r in usage.iterrows():
        season = int(r["season"])
        team = str(r["nfl_team"])
        raw_name = str(r["player_name"])
        key = normalize_alias(raw_name)

        ident = identity_map.get(key, {})
        name = ident.get("player_name") or raw_name
        canonical_player_id = ident.get("canonical_player_id")

        games = max(1, int(r["games"]))
        targets = float(r["targets"])
        carries = float(r["carries"])

        team_attempts = team_passes.get((season, team), 0)
        target_share = round((targets / team_attempts * 100), 2) if team_attempts else 0

        targets_pg = round(targets / games, 2)
        carries_pg = round(carries / games, 2)

        position = ident.get("position")
        sleeper_id = ident.get("sleeper_id")

        red_zone_touches = float(r["red_zone_targets"]) + float(r["red_zone_carries"])

        usage_score = round(
            min(
                100,
                target_share * 1.8
                + targets_pg * 4
                + carries_pg * 3
                + red_zone_touches * 0.8
            ),
            2,
        )

        role_type = infer_role_type(position, carries_pg, targets_pg, target_share)

        summary = (
            f"{name} averaged {carries_pg} carries and {targets_pg} targets per game. "
            f"Target share: {target_share}%. Red-zone touches: {red_zone_touches}."
        )

        rows.append(
            {
                "season": season,
                "canonical_player_id": canonical_player_id,
                "player_name": name,
                "sleeper_id": sleeper_id,
                "nfl_team": team,
                "position": position,

                "games": games,
                "snap_share": None,
                "route_participation": None,
                "target_share": target_share,
                "air_yards_share": None,
                "carries_per_game": carries_pg,
                "targets_per_game": targets_pg,
                "red_zone_touches": red_zone_touches,

                "usage_score": usage_score,
                "role_type": role_type,
                "usage_summary": summary,

                "created_at": now,
            }
        )

    return rows


def main() -> None:
    sb = service_client()

    pbp = load_pbp()
    rows = build_usage_rows(pbp)

    print(f"Built player usage rows: {len(rows)}")

    if rows:
        deduped = {}
        for row in rows:
            key = (row.get("season"), str(row.get("player_name")).strip().lower())

            existing = deduped.get(key)
            if existing is None or float(row.get("usage_score") or 0) > float(existing.get("usage_score") or 0):
                deduped[key] = row

        rows = list(deduped.values())
        print(f"Deduped player usage rows: {len(rows)}")

        sb.table("player_usage_context").upsert(
            rows,
            on_conflict="season,player_name",
        ).execute()

    print(f"Upserted {len(rows)} player usage rows.")


if __name__ == "__main__":
    main()
