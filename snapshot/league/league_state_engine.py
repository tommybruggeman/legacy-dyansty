from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from auth import service_client
from snapshot.strategy.roster_strategy_engine import RosterStrategyEngine
from snapshot.strategy.team_needs_engine import TeamNeedsEngine


@dataclass
class TeamState:
    team_name: str
    player_count: int
    team_direction: str
    primary_needs: List[str]
    secondary_needs: List[str]
    strengths: List[str]
    core_players: List[str]
    trade_candidates: List[str]
    contract_problems: List[str]
    recommended_moves: List[str]


@dataclass
class LeagueStateReport:
    league_name: str
    team_count: int
    teams: List[TeamState]
    league_trade_map: Dict
    summary: str


class LeagueStateEngine:
    """
    League-wide GM intelligence layer.

    Evaluates every team using:
    - RosterStrategyEngine
    - TeamNeedsEngine

    Produces:
    - team direction
    - needs
    - strengths
    - trade candidates
    - basic trade fit map
    """

    def __init__(self, samples: int = 100):
        self.sb = service_client()
        self.roster_engine = RosterStrategyEngine(samples=samples)
        self.needs_engine = TeamNeedsEngine(samples=samples)

    def evaluate_league(self, league_name: str = "Legacy League") -> LeagueStateReport:
        rows = (
            self.sb.table("player_recommendations")
            .select("*")
            .execute()
            .data
            or []
        )

        teams = {}

        for r in rows:
            team = r.get("owner_team_name") or "Unknown"
            teams.setdefault(team, [])
            teams[team].append(self._row_to_player(r))

        team_states = []

        for team_name, players in teams.items():
            roster_report = self.roster_engine.evaluate_roster(team_name, players)
            needs_report = self.needs_engine.evaluate(team_name, players)

            team_states.append(
                TeamState(
                    team_name=team_name,
                    player_count=len(players),
                    team_direction=roster_report.team_direction,
                    primary_needs=needs_report.primary_needs,
                    secondary_needs=needs_report.secondary_needs,
                    strengths=needs_report.strengths,
                    core_players=roster_report.core_players,
                    trade_candidates=roster_report.trade_candidates,
                    contract_problems=roster_report.contract_problems,
                    recommended_moves=roster_report.recommended_moves + needs_report.recommended_focus,
                )
            )

        trade_map = self._build_trade_map(team_states)

        return LeagueStateReport(
            league_name=league_name,
            team_count=len(team_states),
            teams=team_states,
            league_trade_map=trade_map,
            summary=self._summary(team_states, trade_map),
        )

    def _row_to_player(self, r: Dict) -> Dict:
        return {
            "player_name": r.get("player_name"),
            "sleeper_id": r.get("sleeper_id"),
            "pos": r.get("pos"),
            "salary": r.get("salary"),
            "years": r.get("years"),
            "dynasty_asset_score": r.get("dynasty_asset_score"),
            "win_now_score": r.get("win_now_score"),
        }

    def _build_trade_map(self, teams: List[TeamState]) -> Dict:
        """
        Finds simple strength/need trade fits.

        Example:
        Tommy strength QB + needs RB
        Nick strength RB + needs QB
        """
        fits = []

        for buyer in teams:
            for seller in teams:
                if buyer.team_name == seller.team_name:
                    continue

                matching_positions = [
                    pos for pos in buyer.primary_needs + buyer.secondary_needs
                    if pos in seller.strengths
                ]

                reverse_fit = [
                    pos for pos in seller.primary_needs + seller.secondary_needs
                    if pos in buyer.strengths
                ]

                if matching_positions or reverse_fit:
                    fits.append({
                        "team_a": buyer.team_name,
                        "team_b": seller.team_name,
                        "team_a_needs_from_b": matching_positions,
                        "team_b_needs_from_a": reverse_fit,
                    })

        return {
            "fits": fits,
            "fit_count": len(fits),
        }

    def _summary(self, teams: List[TeamState], trade_map: Dict) -> str:
        contenders = [t.team_name for t in teams if t.team_direction == "CONTEND_NOW"]
        retools = [t.team_name for t in teams if t.team_direction == "RETOOL"]
        ascending = [t.team_name for t in teams if t.team_direction == "ASCENDING_BUILD"]

        return (
            f"League scan complete: {len(teams)} teams evaluated. "
            f"Contenders: {', '.join(contenders) if contenders else 'none clearly flagged'}. "
            f"Ascending builds: {', '.join(ascending) if ascending else 'none clearly flagged'}. "
            f"Retool teams: {', '.join(retools) if retools else 'none clearly flagged'}. "
            f"Trade fit pairs found: {trade_map.get('fit_count', 0)}."
        )
