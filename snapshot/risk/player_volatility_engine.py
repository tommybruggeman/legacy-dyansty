from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd

from auth import service_client


@dataclass
class VolatilityProfile:
    player_name: str
    pos: str
    volatility_score: float
    consistency_score: float
    boom_bust_label: str
    confidence: str
    note: str


class PlayerVolatilityEngine:
    """
    Evaluates player reliability vs boom/bust profile.

    V1 uses season-level historical PPG variation.
    Later V2 should use weekly game logs for true weekly volatility.
    """

    def __init__(self):
        self.sb = service_client()

    def evaluate(self, player: Dict) -> VolatilityProfile:
        player_name = player.get("player_name", "Unknown")
        pos = player.get("pos", "RB")
        sleeper_id = str(player.get("sleeper_id") or "")

        nflverse_id = self._resolve_nflverse_id(sleeper_id)

        if not nflverse_id:
            return self._fallback(player_name, pos, "No nflverse_id found.")

        history = self._load_history(nflverse_id)

        if history.empty or "fantasy_ppg_ppr" not in history.columns:
            return self._fallback(player_name, pos, f"No fantasy_ppg_ppr history found for {nflverse_id}.")

        ppg = pd.to_numeric(history["fantasy_ppg_ppr"], errors="coerce").dropna()

        if len(ppg) < 2:
            return self._fallback(player_name, pos, "Not enough history for volatility profile.")

        mean = float(ppg.mean())
        std = float(ppg.std())

        if mean <= 0:
            return self._fallback(player_name, pos, "Zero or invalid historical scoring mean.")

        cv = std / mean

        volatility_score = min(100, cv * 180)
        consistency_score = max(0, 100 - volatility_score)

        label = self._label(volatility_score, mean)

        return VolatilityProfile(
            player_name=player_name,
            pos=pos,
            volatility_score=round(volatility_score, 1),
            consistency_score=round(consistency_score, 1),
            boom_bust_label=label,
            confidence=self._confidence(len(ppg)),
            note=(
                f"V1 volatility from season-to-season PPG variation. "
                f"Mean PPG={round(mean, 2)}, Std={round(std, 2)}, CV={round(cv, 3)}."
            ),
        )

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
            .select("season,fantasy_ppg_ppr,games")
            .eq("sleeper_id", nflverse_id)
            .order("season", desc=True)
            .limit(5)
            .execute()
            .data
            or []
        )

        return pd.DataFrame(rows)

    def _label(self, volatility_score: float, mean_ppg: float) -> str:
        if volatility_score <= 20 and mean_ppg >= 15:
            return "STABLE_CORE"
        if volatility_score <= 30:
            return "RELIABLE_STARTER"
        if volatility_score <= 45 and mean_ppg >= 14:
            return "UPSIDE_WITH_VARIANCE"
        if volatility_score <= 60:
            return "VOLATILE_STARTER"
        return "BOOM_BUST"

    def _confidence(self, n: int) -> str:
        if n >= 4:
            return "HIGH"
        if n >= 3:
            return "MEDIUM"
        return "LOW"

    def _fallback(self, player_name: str, pos: str, note: str) -> VolatilityProfile:
        return VolatilityProfile(
            player_name=player_name,
            pos=pos,
            volatility_score=50.0,
            consistency_score=50.0,
            boom_bust_label="UNKNOWN_VOLATILITY",
            confidence="LOW",
            note=f"Fallback volatility profile: {note}",
        )
