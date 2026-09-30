"""nflverse weekly player stats + snap counts -> nfl_player_stats rows."""
from __future__ import annotations

from io import StringIO
from typing import Any

import pandas as pd

from pipeline.nfl_data.common import now_iso, to_float, to_int, to_text
from pipeline.nfl_data.crosswalk import Crosswalk

STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
SNAPS_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K"}

STAT_COLUMNS = {
    "completions": "completions",
    "attempts": "attempts",
    "passing_yards": "passing_yards",
    "passing_tds": "passing_tds",
    "passing_interceptions": "interceptions",
    "sacks_suffered": "sacks",
    "passing_epa": "passing_epa",
    "carries": "carries",
    "rushing_yards": "rushing_yards",
    "rushing_tds": "rushing_tds",
    "rushing_first_downs": "rushing_first_downs",
    "rushing_epa": "rushing_epa",
    "targets": "targets",
    "receptions": "receptions",
    "receiving_yards": "receiving_yards",
    "receiving_tds": "receiving_tds",
    "receiving_air_yards": "receiving_air_yards",
    "receiving_yards_after_catch": "receiving_yac",
    "receiving_first_downs": "receiving_first_downs",
    "receiving_epa": "receiving_epa",
    "target_share": "target_share",
    "air_yards_share": "air_yards_share",
    "wopr": "wopr",
    "fumbles_lost_total": "fumbles_lost",
    "fantasy_points": "fantasy_points",
    "fantasy_points_ppr": "fantasy_points_ppr",
}


def read_csv(text: str) -> pd.DataFrame:
    return pd.read_csv(StringIO(text), low_memory=False)


def _is_home(game_id: Any, team: Any) -> bool | None:
    # game_id looks like 2025_01_PIT_NYJ : away_home
    parts = str(game_id or "").split("_")
    if len(parts) != 4:
        return None
    return str(team) == parts[3]


def snap_index(snaps: pd.DataFrame | None, crosswalk: Crosswalk) -> dict[tuple[str, int, int, str], tuple[float | None, float | None]]:
    """(gsis_id, season, week, game_type) -> (offense_snaps, offense_pct)."""
    out: dict[tuple[str, int, int, str], tuple[float | None, float | None]] = {}
    if snaps is None or snaps.empty:
        return out
    for _, row in snaps.iterrows():
        gsis = crosswalk.gsis_for_pfr(row.get("pfr_player_id"))
        if not gsis:
            continue
        key = (gsis, to_int(row.get("season")) or 0, to_int(row.get("week")) or 0, to_text(row.get("game_type")) or "REG")
        out[key] = (to_float(row.get("offense_snaps")), to_float(row.get("offense_pct")))
    return out


def transform_stats(stats: pd.DataFrame, snaps: pd.DataFrame | None, crosswalk: Crosswalk) -> list[dict[str, Any]]:
    stamp = now_iso()
    snap_idx = snap_index(snaps, crosswalk)
    rows: list[dict[str, Any]] = []
    for _, r in stats.iterrows():
        pos = to_text(r.get("position"))
        if pos not in KEEP_POSITIONS:
            continue
        gsis = to_text(r.get("player_id"))
        if not gsis:
            continue
        season = to_int(r.get("season"))
        week = to_int(r.get("week"))
        season_type = to_text(r.get("season_type")) or "REG"
        if season is None or week is None:
            continue
        snaps_val, snap_pct = snap_idx.get((gsis, season, week, season_type), (None, None))
        row: dict[str, Any] = {
            "gsis_id": gsis,
            "season": season,
            "week": week,
            "season_type": season_type,
            "sleeper_id": crosswalk.sleeper_for_gsis(gsis),
            "player_name": to_text(r.get("player_display_name")) or to_text(r.get("player_name")),
            "position": pos,
            "team": to_text(r.get("team")),
            "opponent": to_text(r.get("opponent_team")),
            "is_home": _is_home(r.get("game_id"), r.get("team")),
            "offense_snaps": snaps_val,
            "offense_pct": snap_pct,
            "source": "nflverse",
            "refreshed_at": stamp,
        }
        for src, dst in STAT_COLUMNS.items():
            row[dst] = to_float(r.get(src)) if src in stats.columns else None
        rows.append(row)
    return rows
