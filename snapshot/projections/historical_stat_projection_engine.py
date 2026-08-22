from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd

from auth import service_client
from snapshot.projections.player_projection_engine import (
    PlayerProjection,
    PlayerProjectionEngine,
)


@dataclass
class HistoricalProjectionDebug:
    source: str
    seasons_used: int
    note: str


class HistoricalStatProjectionEngine:
    def __init__(self):
        self.sb = service_client()
        self.fallback_engine = PlayerProjectionEngine()

    def project(self, player: Dict) -> PlayerProjection:
        sleeper_id = str(player.get("sleeper_id") or "")
        pos = player.get("pos", "RB")

        nflverse_id = self._resolve_nflverse_id(sleeper_id)

        if not nflverse_id:
            fallback = self.fallback_engine.project(player)
            fallback.projection_note = "Fallback projection: no nflverse_id found in identity map."
            return fallback

        history = self._load_history(nflverse_id)

        if history.empty:
            fallback = self.fallback_engine.project(player)
            fallback.projection_note = f"Fallback projection: no history found for nflverse_id {nflverse_id}."
            return fallback

        stats = self._build_weekly_projection(player, history, pos)

        return PlayerProjection(
            player_name=player.get("player_name", "Unknown"),
            pos=pos,
            projected_stats=stats,
            confidence=self._confidence(history),
            projection_note=(
                f"Historical projection using {len(history)} season row(s), "
                f"nflverse_id={nflverse_id}, weighted toward recent seasons."
            ),
        )

    def _resolve_nflverse_id(self, sleeper_id: str) -> str | None:
        if not sleeper_id:
            return None

        rows = (
            self.sb.table("player_identity_map")
            .select("nflverse_id")
            .eq("sleeper_id", sleeper_id)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not rows:
            return None

        return rows[0].get("nflverse_id")

    def _load_history(self, nflverse_id: str) -> pd.DataFrame:
        rows = (
            self.sb.table("player_season_stats")
            .select("*")
            .eq("sleeper_id", nflverse_id)
            .order("season", desc=True)
            .limit(5)
            .execute()
            .data
            or []
        )

        return pd.DataFrame(rows)

    def _build_weekly_projection(self, player: Dict, history: pd.DataFrame, pos: str) -> Dict:
        history = history.copy()
        history["weight"] = self._weights(len(history))

        def wavg(col: str, default: float = 0.0) -> float:
            if col not in history.columns:
                return default

            vals = pd.to_numeric(history[col], errors="coerce").fillna(0)
            return float((vals * history["weight"]).sum() / history["weight"].sum())

        games = max(wavg("games", 14), 1)

        if pos == "QB":
            return {
                "player_name": player.get("player_name", "Unknown"),
                "pos": "QB",
                "pass_yards": round(wavg("passing_yards") / games, 1),
                "pass_tds": round(wavg("passing_tds") / games, 2),
                "interceptions": round(wavg("interceptions") / games, 2),
                "rush_yards": round(wavg("rushing_yards") / games, 1),
                "rush_tds": round(wavg("rushing_tds") / games, 2),
            }

        return {
            "player_name": player.get("player_name", "Unknown"),
            "pos": pos,
            "rush_yards": round(wavg("rushing_yards") / games, 1),
            "rush_tds": round(wavg("rushing_tds") / games, 2),
            "receptions": round(wavg("receptions") / games, 1),
            "rec_yards": round(wavg("receiving_yards") / games, 1),
            "rec_tds": round(wavg("receiving_tds") / games, 2),
            "fumbles": 0.03,
        }

    def _weights(self, n: int):
        return [5, 4, 3, 2, 1][:n]

    def _confidence(self, history: pd.DataFrame) -> str:
        if len(history) >= 3:
            return "HIGH"
        if len(history) == 2:
            return "MEDIUM"
        return "LOW"
