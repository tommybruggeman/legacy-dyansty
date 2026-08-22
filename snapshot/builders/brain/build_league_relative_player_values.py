from __future__ import annotations

import argparse
from typing import Any

import pandas as pd

from auth import service_client
from snapshot.builders.strategy.build_player_strategic_profiles import _write_scoped_player_rows


TARGET_TABLE = "league_relative_player_values"


def percentile_rank(series: pd.Series) -> pd.Series:
    return series.rank(pct=True).fillna(0) * 100


def build_league_relative_player_values(
    league_id: str,
    league_team_id: str | None = None,
    dry_run: bool = False,
    sb: Any | None = None,
):
    sb = sb or service_client()
    _require_league(sb, league_id)
    if league_team_id:
        _require_league_team(sb, league_id, league_team_id)

    query = sb.table("player_strategic_profiles").select("*").eq("league_id", league_id)
    if league_team_id:
        query = query.eq("league_team_id", league_team_id)

    rows = query.execute().data or []

    print("Requested league_id:", league_id)
    print("Requested league_team_id:", league_team_id or "all")
    print("Loaded strategic profiles:", len(rows))

    if not rows:
        print("Prepared rows: 0")
        print("Skipped rows: 0")
        print("Written rows: 0")
        print("Dry run:", dry_run)
        return {
            "input_count": 0,
            "prepared_count": 0,
            "skipped_count": 0,
            "written_count": 0,
            "inserted_count": 0,
            "updated_count": 0,
        }

    df = pd.DataFrame(rows)
    skipped = 0

    required_cols = ["league_id", "league_team_id", "sleeper_id"]
    missing_scope = df[required_cols].isna().any(axis=1)
    if missing_scope.any():
        skipped = int(missing_scope.sum())
        df = df[~missing_scope].copy()

    for col in ["asset_score", "win_now_score", "median_projection", "opportunity_score"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["overall_value_score"] = (
        df["asset_score"] * 0.35
        + df["win_now_score"] * 0.40
        + df["opportunity_score"] * 0.15
        + df["median_projection"] * 0.10
    )

    df["asset_percentile"] = percentile_rank(df["asset_score"])
    df["win_now_percentile"] = percentile_rank(df["win_now_score"])
    df["opportunity_percentile"] = percentile_rank(df["opportunity_score"])
    df["overall_percentile"] = percentile_rank(df["overall_value_score"])

    df["position_overall_percentile"] = (
        df.groupby("pos")["overall_value_score"]
        .rank(pct=True)
        .fillna(0)
        * 100
    )

    def tier(r):
        overall = r["overall_percentile"]
        pos_pct = r["position_overall_percentile"]

        if overall >= 90 or pos_pct >= 90:
            return "LEAGUE_ELITE"
        if overall >= 75 or pos_pct >= 80:
            return "HIGH_END_STARTER"
        if overall >= 55 or pos_pct >= 60:
            return "STARTER_LEVEL"
        if overall >= 35 or pos_pct >= 40:
            return "DEPTH_VALUE"
        return "REPLACEMENT_LEVEL"

    df["league_value_tier"] = df.apply(tier, axis=1)

    output = []

    for _, r in df.iterrows():
        output.append({
            "league_id": r.get("league_id"),
            "league_team_id": r.get("league_team_id"),
            "owner_team_name": r.get("owner_team_name"),
            "sleeper_id": r.get("sleeper_id"),
            "player_name": r.get("player_name"),
            "pos": r.get("pos"),
            "asset_score": float(r.get("asset_score") or 0),
            "win_now_score": float(r.get("win_now_score") or 0),
            "opportunity_score": float(r.get("opportunity_score") or 0),
            "median_projection": float(r.get("median_projection") or 0),
            "overall_value_score": round(float(r.get("overall_value_score") or 0), 2),
            "asset_percentile": round(float(r.get("asset_percentile") or 0), 2),
            "win_now_percentile": round(float(r.get("win_now_percentile") or 0), 2),
            "opportunity_percentile": round(float(r.get("opportunity_percentile") or 0), 2),
            "overall_percentile": round(float(r.get("overall_percentile") or 0), 2),
            "position_overall_percentile": round(float(r.get("position_overall_percentile") or 0), 2),
            "league_value_tier": r.get("league_value_tier"),
        })

    inserted = 0
    updated = 0
    if output and not dry_run:
        inserted, updated = _write_scoped_player_rows(sb, TARGET_TABLE, output)

    print("Prepared rows:", len(output))
    print("Skipped rows:", skipped)
    print("Written rows:", 0 if dry_run else len(output))
    print("Inserted:", inserted)
    print("Updated:", updated)
    print("Dry run:", dry_run)
    print("Done.")

    return {
        "input_count": len(rows),
        "prepared_count": len(output),
        "skipped_count": skipped,
        "written_count": 0 if dry_run else len(output),
        "inserted_count": inserted,
        "updated_count": updated,
    }


def _require_league(sb: Any, league_id: str) -> None:
    rows = (
        sb.table("leagues")
        .select("id")
        .eq("id", league_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise RuntimeError(f"League not found: {league_id}")


def _require_league_team(sb: Any, league_id: str, league_team_id: str) -> None:
    rows = (
        sb.table("league_teams")
        .select("id")
        .eq("id", league_team_id)
        .eq("league_id", league_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise RuntimeError("league_team_id was not found in the requested league.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build scoped league-relative player values.")
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--league-team-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    build_league_relative_player_values(
        league_id=args.league_id,
        league_team_id=args.league_team_id,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
