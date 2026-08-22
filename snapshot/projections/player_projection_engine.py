from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class PlayerProjection:
    player_name: str
    pos: str
    projected_stats: Dict
    confidence: str
    projection_note: str


class PlayerProjectionEngine:
    """
    Builds baseline stat projections that feed the simulation engine.

    V1 uses available player context + conservative defaults.
    Later versions will plug in:
    - historical weekly stats
    - opponent defense
    - Vegas/team totals
    - home/away
    - scheme
    - offensive line
    - injuries
    - target/rush competition
    """

    def project(self, player: Dict) -> PlayerProjection:
        pos = player.get("pos", "RB")

        if pos == "QB":
            return self._project_qb(player)

        if pos == "RB":
            return self._project_rb(player)

        if pos == "WR":
            return self._project_wr(player)

        if pos == "TE":
            return self._project_te(player)

        return self._project_wr(player)

    def _project_qb(self, player: Dict) -> PlayerProjection:
        asset = float(player.get("dynasty_asset_score", 50) or 50)
        win_now = float(player.get("win_now_score", asset) or asset)

        pass_yards = 180 + (win_now * 2.0)
        pass_tds = 0.6 + (win_now / 45)
        interceptions = max(0.3, 1.4 - (asset / 100))
        rush_yards = max(5, (asset - 45) * 0.8)
        rush_tds = 0.15 if rush_yards >= 20 else 0.05

        stats = {
            "player_name": player.get("player_name", "Unknown"),
            "pos": "QB",
            "pass_yards": round(pass_yards, 1),
            "pass_tds": round(pass_tds, 2),
            "interceptions": round(interceptions, 2),
            "rush_yards": round(rush_yards, 1),
            "rush_tds": round(rush_tds, 2),
        }

        return PlayerProjection(
            player_name=stats["player_name"],
            pos="QB",
            projected_stats=stats,
            confidence="LOW",
            projection_note="V1 projection based on asset/win-now scores only.",
        )

    def _project_rb(self, player: Dict) -> PlayerProjection:
        asset = float(player.get("dynasty_asset_score", 50) or 50)
        win_now = float(player.get("win_now_score", asset) or asset)

        rush_yards = 25 + (win_now * 0.9)
        rush_tds = 0.15 + (win_now / 220)
        receptions = 1.0 + (asset / 45)
        rec_yards = receptions * 7.5
        rec_tds = 0.05 + (asset / 500)

        stats = {
            "player_name": player.get("player_name", "Unknown"),
            "pos": "RB",
            "rush_yards": round(rush_yards, 1),
            "rush_tds": round(rush_tds, 2),
            "receptions": round(receptions, 1),
            "rec_yards": round(rec_yards, 1),
            "rec_tds": round(rec_tds, 2),
            "fumbles": 0.05,
        }

        return PlayerProjection(
            player_name=stats["player_name"],
            pos="RB",
            projected_stats=stats,
            confidence="LOW",
            projection_note="V1 projection based on asset/win-now scores only.",
        )

    def _project_wr(self, player: Dict) -> PlayerProjection:
        asset = float(player.get("dynasty_asset_score", 50) or 50)
        win_now = float(player.get("win_now_score", asset) or asset)

        receptions = 2.0 + (win_now / 20)
        rec_yards = receptions * (8.5 + asset / 25)
        rec_tds = 0.08 + (win_now / 240)

        stats = {
            "player_name": player.get("player_name", "Unknown"),
            "pos": "WR",
            "rush_yards": 0,
            "rush_tds": 0,
            "receptions": round(receptions, 1),
            "rec_yards": round(rec_yards, 1),
            "rec_tds": round(rec_tds, 2),
            "fumbles": 0.03,
        }

        return PlayerProjection(
            player_name=stats["player_name"],
            pos="WR",
            projected_stats=stats,
            confidence="LOW",
            projection_note="V1 projection based on asset/win-now scores only.",
        )

    def _project_te(self, player: Dict) -> PlayerProjection:
        asset = float(player.get("dynasty_asset_score", 50) or 50)
        win_now = float(player.get("win_now_score", asset) or asset)

        receptions = 1.5 + (win_now / 25)
        rec_yards = receptions * (7.0 + asset / 35)
        rec_tds = 0.08 + (win_now / 260)

        stats = {
            "player_name": player.get("player_name", "Unknown"),
            "pos": "TE",
            "rush_yards": 0,
            "rush_tds": 0,
            "receptions": round(receptions, 1),
            "rec_yards": round(rec_yards, 1),
            "rec_tds": round(rec_tds, 2),
            "fumbles": 0.03,
        }

        return PlayerProjection(
            player_name=stats["player_name"],
            pos="TE",
            projected_stats=stats,
            confidence="LOW",
            projection_note="V1 projection based on asset/win-now scores only.",
        )
