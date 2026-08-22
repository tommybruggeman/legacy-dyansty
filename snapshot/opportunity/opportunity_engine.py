from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd

from auth import service_client


@dataclass
class OpportunityProfile:
    player_name: str
    pos: str
    opportunity_score: float
    volume_score: float
    receiving_score: float
    rushing_score: float
    red_zone_score: float
    role: str
    confidence: str
    note: str


class OpportunityEngine:
    """
    Evaluates future fantasy opportunity using historical usage signals.

    V1 inputs:
    - carries
    - targets
    - touches
    - opportunities
    - target_share
    - rushing_first_downs
    - receiving_first_downs
    - player_style
    """

    def __init__(self):
        self.sb = service_client()

    def evaluate(self, player: Dict) -> OpportunityProfile:
        sleeper_id = str(player.get("sleeper_id") or "")
        pos = player.get("pos", "RB")
        player_name = player.get("player_name", "Unknown")

        nflverse_id = self._resolve_nflverse_id(sleeper_id)

        if not nflverse_id:
            return self._fallback(player_name, pos, "No nflverse_id found.")

        history = self._load_history(nflverse_id)

        if history.empty:
            return self._fallback(player_name, pos, f"No usage history found for {nflverse_id}.")

        usage = self._weighted_usage(history)

        if pos == "QB":
            return self._score_qb(player_name, pos, usage, len(history))

        if pos == "RB":
            return self._score_rb(player_name, pos, usage, len(history))

        if pos == "WR":
            return self._score_receiver(player_name, pos, usage, len(history))

        if pos == "TE":
            return self._score_receiver(player_name, pos, usage, len(history))

        return self._score_receiver(player_name, pos, usage, len(history))

    def _resolve_nflverse_id(self, sleeper_id: str) -> str | None:
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

    def _weighted_usage(self, history: pd.DataFrame) -> Dict:
        history = history.copy()
        history["weight"] = [5, 4, 3, 2, 1][: len(history)]

        def wavg(col: str, default: float = 0.0) -> float:
            if col not in history.columns:
                return default
            vals = pd.to_numeric(history[col], errors="coerce").fillna(0)
            return float((vals * history["weight"]).sum() / history["weight"].sum())

        games = max(wavg("games", 14), 1)

        return {
            "games": games,
            "carries_pg": wavg("carries") / games,
            "targets_pg": wavg("targets") / games,
            "touches_pg": wavg("touches") / games,
            "opportunities_pg": wavg("opportunities") / games,
            "target_share": wavg("target_share"),
            "rush_fd_pg": wavg("rushing_first_downs") / games,
            "rec_fd_pg": wavg("receiving_first_downs") / games,
            "pass_attempts_pg": wavg("passing_attempts") / games,
            "completions_pg": wavg("completions") / games,
            "player_style": history.iloc[0].get("player_style"),
        }

    def _score_qb(self, player_name: str, pos: str, u: Dict, seasons: int) -> OpportunityProfile:
        pass_volume = min(100, u["pass_attempts_pg"] / 38 * 100)
        rush_volume = min(100, u["carries_pg"] / 8 * 100)
        red_zone_proxy = min(100, u["rush_fd_pg"] / 3 * 100)

        opportunity = (
            pass_volume * 0.50
            + rush_volume * 0.35
            + red_zone_proxy * 0.15
        )

        return OpportunityProfile(
            player_name=player_name,
            pos=pos,
            opportunity_score=round(opportunity, 1),
            volume_score=round(pass_volume, 1),
            receiving_score=0.0,
            rushing_score=round(rush_volume, 1),
            red_zone_score=round(red_zone_proxy, 1),
            role=self._role_label(pos, opportunity),
            confidence=self._confidence(seasons),
            note=f"QB opportunity from pass attempts, rushing usage, and rushing first downs. Style={u['player_style']}",
        )

    def _score_rb(self, player_name: str, pos: str, u: Dict, seasons: int) -> OpportunityProfile:
        rushing = min(100, u["carries_pg"] / 18 * 100)
        receiving = min(100, u["targets_pg"] / 6 * 100)
        volume = min(100, u["opportunities_pg"] / 22 * 100)
        red_zone_proxy = min(100, u["rush_fd_pg"] / 3 * 100)

        opportunity = (
            volume * 0.35
            + rushing * 0.30
            + receiving * 0.20
            + red_zone_proxy * 0.15
        )

        return OpportunityProfile(
            player_name=player_name,
            pos=pos,
            opportunity_score=round(opportunity, 1),
            volume_score=round(volume, 1),
            receiving_score=round(receiving, 1),
            rushing_score=round(rushing, 1),
            red_zone_score=round(red_zone_proxy, 1),
            role=self._role_label(pos, opportunity),
            confidence=self._confidence(seasons),
            note=f"RB opportunity from carries, targets, total opportunities, and first-down rushing role. Style={u['player_style']}",
        )

    def _score_receiver(self, player_name: str, pos: str, u: Dict, seasons: int) -> OpportunityProfile:
        receiving = min(100, u["targets_pg"] / 10 * 100)
        target_share = min(100, u["target_share"] / 0.28 * 100)
        volume = min(100, u["opportunities_pg"] / 10 * 100)
        red_zone_proxy = min(100, u["rec_fd_pg"] / 4 * 100)

        opportunity = (
            receiving * 0.35
            + target_share * 0.30
            + volume * 0.20
            + red_zone_proxy * 0.15
        )

        return OpportunityProfile(
            player_name=player_name,
            pos=pos,
            opportunity_score=round(opportunity, 1),
            volume_score=round(volume, 1),
            receiving_score=round(receiving, 1),
            rushing_score=0.0,
            red_zone_score=round(red_zone_proxy, 1),
            role=self._role_label(pos, opportunity),
            confidence=self._confidence(seasons),
            note=f"{pos} opportunity from targets, target share, total opportunity, and receiving first downs. Style={u['player_style']}",
        )

    def _role_label(self, pos: str, score: float) -> str:
        if score >= 85:
            return "ELITE_USAGE"
        if score >= 70:
            return "STRONG_STARTER_USAGE"
        if score >= 55:
            return "STARTER_USAGE"
        if score >= 40:
            return "ROTATION_USAGE"
        return "LOW_USAGE"

    def _confidence(self, seasons: int) -> str:
        if seasons >= 3:
            return "HIGH"
        if seasons == 2:
            return "MEDIUM"
        return "LOW"

    def _fallback(self, player_name: str, pos: str, note: str) -> OpportunityProfile:
        return OpportunityProfile(
            player_name=player_name,
            pos=pos,
            opportunity_score=50.0,
            volume_score=50.0,
            receiving_score=50.0 if pos in ["RB", "WR", "TE"] else 0.0,
            rushing_score=50.0 if pos in ["QB", "RB"] else 0.0,
            red_zone_score=50.0,
            role="UNKNOWN_USAGE",
            confidence="LOW",
            note=f"Fallback opportunity profile: {note}",
        )
