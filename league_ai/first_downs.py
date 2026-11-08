"""Our own first-down projection.

Legacy scores first downs (rec_fd, rush_fd). Checked stat by stat on 2026 weeks 1-8 (scoring/projection_by_stat.py),
the projected stat lines we start from put about 2.4x too many first-down points on RBs, WRs and TEs; every other stat
was close. That one stat was most of why team projections ran ~16 points high, and the calibration line had to flatten
every projection to make up for it (big projections most).

So we project first downs ourselves: projected catches x the player's first downs per catch, and projected carries x
his first downs per carry, each rate leaning on his position's rate until he has enough touches of his own. Rates come
only from real stat lines of finished weeks (our own official lines, nflverse). Touchdowns don't count as first downs
(Sleeper's convention, same as our scoring engine).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

# starting rates before any real games are counted (first downs per catch / per carry, touchdowns excluded)
DEFAULT = {"QB": (0.0, 0.30), "RB": (0.30, 0.20), "WR": (0.55, 0.25), "TE": (0.55, 0.30)}
POS_PRIOR = 200.0    # position rate: worth 200 catches/carries of the default until real ones pile up
PLAYER_PRIOR = 25.0  # a player's own rate: shrunk toward his position's with 25 catches/carries of weight
POSITIONS = tuple(DEFAULT)


def _f(v: Any) -> float:
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


class FirstDowns:
    def __init__(self) -> None:
        self.pos: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])      # fd_rec, rec, fd_rush, rush_att
        self.player: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        self.positions: dict[str, str] = {}

    def add(self, pid: str, pos: str, stats: Mapping[str, Any] | None) -> None:
        """Count one real stat line."""
        pos = str(pos or "").upper()
        if pos not in DEFAULT or not stats:
            return
        v = (_f(stats.get("rec_fd")), _f(stats.get("rec")), _f(stats.get("rush_fd")), _f(stats.get("rush_att")))
        if not (v[1] or v[3]):
            return
        self.positions[str(pid)] = pos
        for bucket in (self.pos[pos], self.player[str(pid)]):
            for i, x in enumerate(v):
                bucket[i] += x

    def rates(self, pid: str, pos: str) -> tuple[float, float]:
        pos = str(pos or "").upper()
        d = DEFAULT.get(pos, (0.5, 0.25))
        p = self.pos.get(pos) or [0.0, 0.0, 0.0, 0.0]
        pr = ((p[0] + POS_PRIOR * d[0]) / (p[1] + POS_PRIOR), (p[2] + POS_PRIOR * d[1]) / (p[3] + POS_PRIOR))
        me = self.player.get(str(pid)) or [0.0, 0.0, 0.0, 0.0]
        return ((me[0] + PLAYER_PRIOR * pr[0]) / (me[1] + PLAYER_PRIOR), (me[2] + PLAYER_PRIOR * pr[1]) / (me[3] + PLAYER_PRIOR))

    def adjust(self, pid: str, pos: str, stats: Mapping[str, Any] | None) -> dict[str, Any]:
        """A projected stat line with our first downs in place of the projected ones."""
        out = dict(stats or {})
        if str(pos or "").upper() not in DEFAULT:
            return out
        r_rec, r_rush = self.rates(pid, pos)
        out["rec_fd"] = round(_f(out.get("rec")) * r_rec, 3)
        out["rush_fd"] = round(_f(out.get("rush_att")) * r_rush, 3)
        return out

    def to_card(self) -> dict[str, Any]:
        """What the app needs: each position's rate and every player's own (rounded), not the raw counts."""
        return {"pos": {p: [round(x, 4) for x in self.rates("", p)] for p in DEFAULT},
                "players": {pid: [round(x, 4) for x in self.rates(pid, pos)] for pid, pos in self.positions.items()}}


def adjust_with_card(card: Mapping[str, Any] | None, pid: str, pos: str, stats: Mapping[str, Any] | None) -> dict[str, Any]:
    """The live side (api.projections.week_projections): saved rates from the daily calibration job. Without a saved
    card the projected line is left as it is."""
    out = dict(stats or {})
    pos = str(pos or "").upper()
    if not card or pos not in DEFAULT:
        return out
    rates = (card.get("players") or {}).get(str(pid)) or (card.get("pos") or {}).get(pos)
    if not rates or len(rates) != 2:
        return out
    out["rec_fd"] = round(_f(out.get("rec")) * float(rates[0]), 3)
    out["rush_fd"] = round(_f(out.get("rush_att")) * float(rates[1]), 3)
    return out
