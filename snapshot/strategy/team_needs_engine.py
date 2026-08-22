from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from snapshot.strategy.player_strategic_profile_engine import PlayerStrategicProfileEngine


@dataclass
class PositionNeed:
    position: str
    need_level: str
    roster_count: int
    required_starters: int
    starter_strength: float
    depth_strength: float
    contract_risk: float
    volatility_risk: float
    need_score: float
    note: str


@dataclass
class TeamNeedsReport:
    team_name: str
    primary_needs: List[str]
    secondary_needs: List[str]
    strengths: List[str]
    position_reports: List[PositionNeed]
    recommended_focus: List[str]


class TeamNeedsEngine:
    """
    Evaluates positional roster needs intelligently.

    V2:
    - Uses league roster requirements
    - Separates starter need from depth need
    - Scores contract risk
    - Scores volatility risk
    - Produces a need_score instead of hardcoded labels
    """

    STARTER_COUNTS = {
        "QB": 2,   # Superflex league
        "RB": 2,
        "WR": 3,  # 2 WR + FLEX pressure
        "TE": 1,
    }

    MIN_DEPTH_COUNTS = {
        "QB": 3,
        "RB": 4,
        "WR": 5,
        "TE": 2,
    }

    def __init__(self, samples: int = 250):
        self.player_engine = PlayerStrategicProfileEngine(samples=samples)

    def evaluate(self, team_name: str, players: List[Dict]) -> TeamNeedsReport:
        profiles = [self.player_engine.evaluate(p) for p in players]

        position_reports = [
            self._evaluate_position(pos, [p for p in profiles if p.pos == pos])
            for pos in ["QB", "RB", "WR", "TE"]
        ]

        primary = [
            r.position for r in position_reports
            if r.need_level in ["CRITICAL_STARTER_NEED", "STARTER_NEED"]
        ]

        secondary = [
            r.position for r in position_reports
            if r.need_level in ["DEPTH_NEED", "CONTRACT_NEED", "RISK_NEED", "MONITOR"]
        ]

        strengths = [
            r.position for r in position_reports
            if r.need_level == "POSITION_STRENGTH"
        ]

        return TeamNeedsReport(
            team_name=team_name,
            primary_needs=primary,
            secondary_needs=secondary,
            strengths=strengths,
            position_reports=position_reports,
            recommended_focus=self._recommended_focus(position_reports),
        )

    def _evaluate_position(self, pos: str, group) -> PositionNeed:
        required = self.STARTER_COUNTS[pos]
        min_depth = self.MIN_DEPTH_COUNTS[pos]
        count = len(group)

        if count == 0:
            return PositionNeed(
                position=pos,
                need_level="CRITICAL_STARTER_NEED",
                roster_count=0,
                required_starters=required,
                starter_strength=0.0,
                depth_strength=0.0,
                contract_risk=100.0,
                volatility_risk=100.0,
                need_score=100.0,
                note=f"No {pos} players rostered. This is a critical starter need.",
            )

        ranked = sorted(
            group,
            key=lambda p: (p.win_now_score, p.asset_score, p.opportunity_score),
            reverse=True,
        )

        starters = ranked[:required]
        depth = ranked[required:]

        starter_strength = self._avg([self._player_strength(p) for p in starters])
        depth_strength = self._avg([self._player_strength(p) for p in depth]) if depth else 0.0
        contract_risk = self._avg([self._contract_risk(p) for p in group])
        volatility_risk = self._avg([self._volatility_risk(p) for p in group])

        count_gap = max(0, required - count)
        depth_gap = max(0, min_depth - count)

        need_score = self._need_score(
            starter_strength=starter_strength,
            depth_strength=depth_strength,
            contract_risk=contract_risk,
            volatility_risk=volatility_risk,
            count_gap=count_gap,
            depth_gap=depth_gap,
        )

        need_level = self._need_label(
            need_score=need_score,
            count=count,
            required=required,
            starter_strength=starter_strength,
            depth_strength=depth_strength,
            contract_risk=contract_risk,
            volatility_risk=volatility_risk,
        )

        note = self._note(
            pos=pos,
            count=count,
            required=required,
            min_depth=min_depth,
            starter_strength=starter_strength,
            depth_strength=depth_strength,
            contract_risk=contract_risk,
            volatility_risk=volatility_risk,
            need_score=need_score,
            need_level=need_level,
        )

        return PositionNeed(
            position=pos,
            need_level=need_level,
            roster_count=count,
            required_starters=required,
            starter_strength=round(starter_strength, 1),
            depth_strength=round(depth_strength, 1),
            contract_risk=round(contract_risk, 1),
            volatility_risk=round(volatility_risk, 1),
            need_score=round(need_score, 1),
            note=note,
        )

    def _player_strength(self, p) -> float:
        return (
            p.asset_score * 0.35
            + p.win_now_score * 0.45
            + p.opportunity_score * 0.20
        )

    def _contract_risk(self, p) -> float:
        return {
            "BAD_CONTRACT": 100,
            "OVERPAID": 80,
            "LONG_TERM_RISK": 70,
            "NO_CONTRACT_DATA": 50,
            "FAIR_CONTRACT": 35,
            "VALUE_CONTRACT": 10,
        }.get(p.contract_flag, 50)

    def _volatility_risk(self, p) -> float:
        return {
            "STABLE_CORE": 10,
            "RELIABLE_STARTER": 25,
            "UPSIDE_WITH_VARIANCE": 45,
            "VOLATILE_STARTER": 65,
            "BOOM_BUST": 85,
            "UNKNOWN_VOLATILITY": 50,
        }.get(p.volatility_label, 50)

    def _need_score(
        self,
        starter_strength: float,
        depth_strength: float,
        contract_risk: float,
        volatility_risk: float,
        count_gap: int,
        depth_gap: int,
    ) -> float:
        starter_weakness = max(0, 75 - starter_strength)
        depth_weakness = max(0, 60 - depth_strength)

        score = (
            starter_weakness * 0.35
            + depth_weakness * 0.25
            + contract_risk * 0.18
            + volatility_risk * 0.12
            + count_gap * 25
            + depth_gap * 8
        )

        return min(100, max(0, score))

    def _need_label(
        self,
        need_score: float,
        count: int,
        required: int,
        starter_strength: float,
        depth_strength: float,
        contract_risk: float,
        volatility_risk: float,
    ) -> str:
        if count < required:
            return "STARTER_NEED"

        if starter_strength < 55:
            return "STARTER_NEED"

        if need_score >= 75:
            return "CRITICAL_STARTER_NEED"

        if contract_risk >= 75:
            return "CONTRACT_NEED"

        if volatility_risk >= 70:
            return "RISK_NEED"

        if depth_strength < 45:
            return "DEPTH_NEED"

        if need_score >= 45:
            return "MONITOR"

        if starter_strength >= 75 and depth_strength >= 55 and contract_risk < 60:
            return "POSITION_STRENGTH"

        return "STABLE"

    def _recommended_focus(self, reports: List[PositionNeed]) -> List[str]:
        focus = []

        ordered = sorted(reports, key=lambda r: r.need_score, reverse=True)

        for r in ordered:
            if r.need_level == "CRITICAL_STARTER_NEED":
                focus.append(f"{r.position}: urgent starter acquisition needed.")
            elif r.need_level == "STARTER_NEED":
                focus.append(f"{r.position}: add a starting-caliber option.")
            elif r.need_level == "DEPTH_NEED":
                focus.append(f"{r.position}: add depth behind the starter group.")
            elif r.need_level == "CONTRACT_NEED":
                focus.append(f"{r.position}: improve contract efficiency or explore exits.")
            elif r.need_level == "RISK_NEED":
                focus.append(f"{r.position}: add safer depth to offset volatility risk.")
            elif r.need_level == "MONITOR":
                focus.append(f"{r.position}: monitor market; not urgent, but upgrade if value appears.")
            elif r.need_level == "POSITION_STRENGTH":
                focus.append(f"{r.position}: position of strength; use as trade leverage if needed.")

        return focus[:6] if focus else ["Roster needs are balanced; focus on value trades."]

    def _note(
        self,
        pos: str,
        count: int,
        required: int,
        min_depth: int,
        starter_strength: float,
        depth_strength: float,
        contract_risk: float,
        volatility_risk: float,
        need_score: float,
        need_level: str,
    ) -> str:
        return (
            f"{pos} evaluated as {need_level}. "
            f"Rostered {count}, required starters {required}, preferred depth {min_depth}. "
            f"Starter strength {round(starter_strength,1)}, depth strength {round(depth_strength,1)}, "
            f"contract risk {round(contract_risk,1)}, volatility risk {round(volatility_risk,1)}, "
            f"need score {round(need_score,1)}."
        )

    def _avg(self, values):
        values = [v for v in values if v is not None]
        if not values:
            return 0.0
        return sum(values) / len(values)
