"""nflverse schedule -> nfl_games rows (scores, Vegas lines, roof, weather, coaches)."""
from __future__ import annotations

from io import StringIO
from typing import Any

import pandas as pd

from pipeline.nfl_data.common import now_iso, to_float, to_int, to_text

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


def read_csv(text: str) -> pd.DataFrame:
    return pd.read_csv(StringIO(text), low_memory=False)


def transform_games(frame: pd.DataFrame, seasons: list[int]) -> list[dict[str, Any]]:
    wanted = {int(s) for s in seasons}
    stamp = now_iso()
    rows: list[dict[str, Any]] = []
    for r in frame.to_dict("records"):
        season = to_int(r.get("season"))
        game_id = to_text(r.get("game_id"))
        if season not in wanted or not game_id:
            continue
        div = to_int(r.get("div_game"))
        rows.append({
            "game_id": game_id,
            "season": season,
            "game_type": to_text(r.get("game_type")),
            "week": to_int(r.get("week")),
            "gameday": to_text(r.get("gameday")),
            "gametime": to_text(r.get("gametime")),
            "away_team": to_text(r.get("away_team")),
            "home_team": to_text(r.get("home_team")),
            "away_score": to_int(r.get("away_score")),
            "home_score": to_int(r.get("home_score")),
            "spread_line": to_float(r.get("spread_line")),
            "total_line": to_float(r.get("total_line")),
            "roof": to_text(r.get("roof")),
            "surface": to_text(r.get("surface")),
            "temp": to_float(r.get("temp")),
            "wind": to_float(r.get("wind")),
            "away_coach": to_text(r.get("away_coach")),
            "home_coach": to_text(r.get("home_coach")),
            "away_qb_name": to_text(r.get("away_qb_name")),
            "home_qb_name": to_text(r.get("home_qb_name")),
            "stadium": to_text(r.get("stadium")),
            "div_game": None if div is None else bool(div),
            "refreshed_at": stamp,
        })
    return rows
