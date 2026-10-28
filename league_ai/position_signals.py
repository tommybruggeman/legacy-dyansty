"""Extra start/sit signals to test against the engine, position by position. No model call.

QB and TE are the weakest start/sit positions (graded on every saved lineup), and close calls everywhere sit near a coin
flip. Each signal here is a small multiplier or nudge on the engine's number, built only from games before the week:

- qb_rush: rushing points per game (a QB who runs has a floor passing-only QBs don't)
- qb_pressure: how often the defense sacks QBs per dropback, vs the league
- qb_pass_d: passing EPA per dropback the defense allows, vs the league
- role_trend: share of targets (and snaps) over the last two games vs the season, for TE / WR / RB
- floor: points-per-game spread (a steady player over a boom-or-bust one when the numbers are close)
- form: last two games vs season average

start_sit_grade runs them as variants; nothing here is used live until it grades better.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import pstdev
from typing import Any, Iterable, Mapping

COLUMNS = ("gsis_id, sleeper_id, week, team, opponent, position, attempts, sacks, passing_epa, carries, rushing_yards, rushing_tds, "
           "targets, target_share, offense_pct, fantasy_points_ppr")
DEFENSE_PRIOR_DROPBACKS = 120   # dropbacks of league-average play mixed into each defense
CAP = 0.15


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class Signals:
    """Built once per season-week from the stat rows of earlier games."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]):
        self.games: dict[str, list[dict[str, float]]] = defaultdict(list)   # sleeper id -> per-game numbers, in week order
        d_drop: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])   # defense -> [dropbacks, sacks, epa]
        for r in sorted(rows, key=lambda r: int(r.get("week") or 0)):
            sid = str(r.get("sleeper_id") or "")
            pos = str(r.get("position") or "").upper()
            if sid:
                self.games[sid].append({
                    "pts": _f(r.get("fantasy_points_ppr")), "rush": 0.1 * _f(r.get("rushing_yards")) + 6 * _f(r.get("rushing_tds")),
                    "share": _f(r.get("target_share")), "snaps": _f(r.get("offense_pct")), "targets": _f(r.get("targets"))})
            if pos == "QB" and r.get("opponent"):
                drop = _f(r.get("attempts")) + _f(r.get("sacks"))
                d = d_drop[str(r["opponent"])]
                d[0] += drop
                d[1] += _f(r.get("sacks"))
                d[2] += _f(r.get("passing_epa"))
        tot = [sum(v[i] for v in d_drop.values()) for i in range(3)]
        self.lg_sack = tot[1] / tot[0] if tot[0] else 0.065
        self.lg_epa = tot[2] / tot[0] if tot[0] else 0.0
        k = DEFENSE_PRIOR_DROPBACKS
        self.d_sack = {d: (v[1] + self.lg_sack * k) / (v[0] + k) for d, v in d_drop.items()}
        self.d_epa = {d: (v[2] + self.lg_epa * k) / (v[0] + k) for d, v in d_drop.items()}

    def qb_rush(self, sid: str) -> float:
        g = self.games.get(sid) or []
        return sum(x["rush"] for x in g) / len(g) if g else 0.0

    def qb_pressure(self, opponent: str) -> float:
        """Multiplier: above 1 when the defense sacks less than the league, below 1 when it sacks more."""
        s = self.d_sack.get(opponent)
        if s is None or not self.lg_sack:
            return 1.0
        return max(1 - CAP, min(1 + CAP, 1 - (s / self.lg_sack - 1) * 0.5))

    def qb_pass_d(self, opponent: str) -> float:
        """Multiplier from passing EPA per dropback allowed (0.1 EPA per dropback is a big gap)."""
        e = self.d_epa.get(opponent)
        if e is None:
            return 1.0
        return max(1 - CAP, min(1 + CAP, 1 + (e - self.lg_epa) * 1.5))

    def role_trend(self, sid: str, key: str = "share") -> float:
        """Last two games' share over the season's (1.0 with fewer than 3 games or no role)."""
        g = self.games.get(sid) or []
        if len(g) < 3:
            return 1.0
        season = sum(x[key] for x in g) / len(g)
        recent = sum(x[key] for x in g[-2:]) / 2
        if season <= 0:
            return 1.0
        return max(1 - CAP, min(1 + CAP, recent / season))

    def spread(self, sid: str) -> float | None:
        g = [x["pts"] for x in self.games.get(sid) or []]
        return pstdev(g) if len(g) >= 3 else None

    def form(self, sid: str) -> float:
        g = [x["pts"] for x in self.games.get(sid) or []]
        if len(g) < 3:
            return 1.0
        season = sum(g) / len(g)
        return max(1 - CAP, min(1 + CAP, (sum(g[-2:]) / 2) / season)) if season > 0 else 1.0
