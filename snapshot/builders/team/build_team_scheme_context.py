from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from auth import service_client


SEASONS = [2024]


def _num(s, default=0):
    return pd.to_numeric(s, errors="coerce").fillna(default)


def load_pbp() -> pd.DataFrame:
    frames = []

    for season in SEASONS:
        url = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
        print(f"Loading nflverse PBP CSV: {url}")
        frames.append(pd.read_csv(url, compression="gzip", low_memory=False))

    return pd.concat(frames, ignore_index=True)


def build_scheme_rows(pbp: pd.DataFrame) -> list[dict]:
    df = pbp.copy()

    df = df[df["posteam"].notna()].copy()
    df = df[df["play_type"].isin(["pass", "run"])].copy()

    df["is_pass"] = (df["play_type"] == "pass").astype(int)
    df["is_run"] = (df["play_type"] == "run").astype(int)
    df["is_red_zone"] = (_num(df.get("yardline_100")) <= 20).astype(int)

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for (season, team), g in df.groupby(["season", "posteam"]):
        plays = len(g)
        if plays == 0:
            continue

        passes = int(g["is_pass"].sum())
        runs = int(g["is_run"].sum())

        rz = g[g["is_red_zone"] == 1]
        rz_plays = len(rz)

        targets = g[g["play_type"] == "pass"].copy()

        def target_share(pos: str) -> float:
            if targets.empty or "receiver_position" not in targets.columns:
                return 0.0
            return round(
                (targets["receiver_position"].astype(str).str.upper() == pos).mean() * 100,
                2,
            )

        if "rusher_position" in g.columns:
            qb_runs = g[
                (g["play_type"] == "run")
                & (g["rusher_position"].astype(str).str.upper() == "QB")
            ]
        else:
            qb_runs = g.iloc[0:0]

        games = max(1, g["week"].nunique())

        row = {
            "season": int(season),
            "nfl_team": str(team),

            "pass_rate": round(passes / plays * 100, 2),
            "rush_rate": round(runs / plays * 100, 2),
            "neutral_pass_rate": round(passes / plays * 100, 2),

            "red_zone_rush_rate": round((rz["is_run"].sum() / rz_plays * 100), 2) if rz_plays else 0,
            "red_zone_pass_rate": round((rz["is_pass"].sum() / rz_plays * 100), 2) if rz_plays else 0,

            "rb_target_share": target_share("RB"),
            "wr_target_share": target_share("WR"),
            "te_target_share": target_share("TE"),

            "slot_target_rate": None,
            "deep_target_rate": None,
            "short_area_target_rate": None,

            "qb_rush_attempts_per_game": round(len(qb_runs) / games, 2),
            "pace_score": round(plays / games, 2),
            "offensive_line_score": None,

            "scheme_summary": None,
            "created_at": now,
        }

        row["scheme_summary"] = (
            f"{team} ran {row['pass_rate']}% pass / {row['rush_rate']}% run. "
            f"Targets: WR {row['wr_target_share']}%, RB {row['rb_target_share']}%, TE {row['te_target_share']}%. "
            f"Red zone: {row['red_zone_rush_rate']}% run."
        )

        rows.append(row)

    return rows


def main() -> None:
    sb = service_client()

    pbp = load_pbp()
    rows = build_scheme_rows(pbp)

    print(f"Built team scheme rows: {len(rows)}")

    if rows:
        sb.table("team_scheme_context").upsert(
            rows,
            on_conflict="season,nfl_team",
        ).execute()

    print(f"Upserted {len(rows)} team scheme rows.")


if __name__ == "__main__":
    main()
