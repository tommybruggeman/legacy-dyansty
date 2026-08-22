from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from snapshot.strategy.player_strategic_profile_engine import (
    PlayerStrategicProfile,
    PlayerStrategicProfileEngine,
)


@dataclass
class RosterStrategyReport:
    team_name: str
    team_direction: str
    core_players: List[str]
    win_now_anchors: List[str]
    contract_problems: List[str]
    trade_candidates: List[str]
    replaceable_depth: List[str]
    recommended_moves: List[str]
    player_profiles: List[PlayerStrategicProfile]
    explanation: str


class RosterStrategyEngine:
    """
    Evaluates an entire roster like a GM.

    Uses player strategic profiles to identify:
    - Core players
    - Win-now anchors
    - Contract problems
    - Trade candidates
    - Replaceable depth
    - Team direction
    """

    def __init__(self, samples: int = 300):
        self.player_engine = PlayerStrategicProfileEngine(samples=samples)

    def evaluate_roster(self, team_name: str, players: List[Dict]) -> RosterStrategyReport:
        profiles = [self.player_engine.evaluate(p) for p in players]

        core = self._players_with_labels(profiles, ["CORE_BUILDING_BLOCK"])
        win_now = self._players_with_labels(
            profiles,
            ["WIN_NOW_HAMMER_WITH_RISK", "WIN_NOW_STARTER"],
        )
        contract_problems = self._players_with_labels(profiles, ["CONTRACT_PROBLEM"])
        replaceable = self._players_with_labels(
            profiles,
            ["REPLACEABLE_ASSET", "LOW_IMPACT_DEPTH"],
        )

        trade_candidates = self._trade_candidates(profiles)
        direction = self._team_direction(profiles)
        moves = self._recommended_moves(
            direction=direction,
            core=core,
            win_now=win_now,
            contract_problems=contract_problems,
            replaceable=replaceable,
            trade_candidates=trade_candidates,
        )

        explanation = self._explain(
            team_name=team_name,
            direction=direction,
            core=core,
            win_now=win_now,
            contract_problems=contract_problems,
            trade_candidates=trade_candidates,
            replaceable=replaceable,
        )

        return RosterStrategyReport(
            team_name=team_name,
            team_direction=direction,
            core_players=core,
            win_now_anchors=win_now,
            contract_problems=contract_problems,
            trade_candidates=trade_candidates,
            replaceable_depth=replaceable,
            recommended_moves=moves,
            player_profiles=profiles,
            explanation=explanation,
        )

    def _players_with_labels(self, profiles: List[PlayerStrategicProfile], labels: List[str]) -> List[str]:
        return [
            p.player_name
            for p in profiles
            if p.strategic_label in labels
        ]

    def _trade_candidates(self, profiles: List[PlayerStrategicProfile]) -> List[str]:
        candidates = []

        for p in profiles:
            if p.strategic_label in [
                "CONTRACT_PROBLEM",
                "REPLACEABLE_ASSET",
                "LOW_IMPACT_DEPTH",
            ]:
                candidates.append(p.player_name)
                continue

            if p.asset_score >= 75 and p.win_now_score < 60:
                candidates.append(p.player_name)
                continue

            if p.contract_flag in ["BAD_CONTRACT", "OVERPAID"]:
                candidates.append(p.player_name)

        return list(dict.fromkeys(candidates))

    def _team_direction(self, profiles: List[PlayerStrategicProfile]) -> str:
        if not profiles:
            return "UNKNOWN"

        avg_win_now = sum(p.win_now_score for p in profiles) / len(profiles)
        avg_asset = sum(p.asset_score for p in profiles) / len(profiles)

        core_count = len([p for p in profiles if p.strategic_label == "CORE_BUILDING_BLOCK"])
        win_now_count = len([
            p for p in profiles
            if p.strategic_label in ["WIN_NOW_HAMMER_WITH_RISK", "WIN_NOW_STARTER"]
        ])
        problem_count = len([
            p for p in profiles
            if p.strategic_label in ["CONTRACT_PROBLEM", "REPLACEABLE_ASSET", "LOW_IMPACT_DEPTH"]
        ])

        if core_count >= 3 and avg_win_now >= 70:
            return "CONTEND_NOW"

        if avg_asset >= 70 and avg_win_now < 65:
            return "ASCENDING_BUILD"

        if problem_count >= 4 and avg_win_now < 60:
            return "RETOOL"

        if avg_win_now >= 65 and problem_count <= 3:
            return "COMPETE_WITH_TARGETED_FIXES"

        return "BALANCED / EVALUATE MARKET"

    def _recommended_moves(
        self,
        direction: str,
        core: List[str],
        win_now: List[str],
        contract_problems: List[str],
        replaceable: List[str],
        trade_candidates: List[str],
    ) -> List[str]:
        moves = []

        if direction == "CONTEND_NOW":
            moves.append("Protect the core and use depth/contracts to add weekly starters.")
            moves.append("Prioritize moves that increase playoff-week scoring, not long-term value only.")

        if direction == "ASCENDING_BUILD":
            moves.append("Hold young core assets unless offered a major overpay.")
            moves.append("Shop older win-now pieces if they do not match the long-term window.")

        if direction == "RETOOL":
            moves.append("Shop expensive non-core contracts for picks, younger assets, or cap relief.")
            moves.append("Avoid adding long-term salary unless the player becomes a future core piece.")

        if direction == "COMPETE_WITH_TARGETED_FIXES":
            moves.append("Identify one or two weak roster slots and consolidate depth into upgrades.")

        if contract_problems:
            moves.append(f"Actively explore exits or restructures for: {', '.join(contract_problems[:5])}.")

        if replaceable:
            moves.append(f"Churn or replace low-impact depth: {', '.join(replaceable[:5])}.")

        if trade_candidates:
            moves.append(f"Start market checks on: {', '.join(trade_candidates[:5])}.")

        if not moves:
            moves.append("Hold roster steady and monitor market inefficiencies.")

        return moves

    def _explain(
        self,
        team_name: str,
        direction: str,
        core: List[str],
        win_now: List[str],
        contract_problems: List[str],
        trade_candidates: List[str],
        replaceable: List[str],
    ) -> str:
        return (
            f"{team_name} profiles as {direction}. "
            f"Core players: {', '.join(core) if core else 'none identified'}. "
            f"Win-now anchors: {', '.join(win_now) if win_now else 'none identified'}. "
            f"Contract problems: {', '.join(contract_problems) if contract_problems else 'none flagged'}. "
            f"Trade candidates: {', '.join(trade_candidates) if trade_candidates else 'none flagged'}. "
            f"Replaceable depth: {', '.join(replaceable) if replaceable else 'none flagged'}."
        )
