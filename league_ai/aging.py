"""How much of a player's value carries into each of the next three seasons, by position and age.

Fit on real seasons (nflverse, league scoring): for players who scored above replacement in season s, the share of
that value they produced in s+1, s+2 and s+3, by age that September. Each age is shrunk toward the position's
overall rate, then forced to never rise with age (an older player never holds value better than a younger one).

Used two ways:
- `age_tilt` nudges a rank-based projection for a player's age. Dynasty rankings already price age, so only half
  the gap is applied: on 2020-2025 seasons the fit never saw it removed about a third of the bot's low bias on
  later years and ordered year-3 values slightly better, at about $0.20 more average miss;
- `retention` answers "how long will this guy hold up" for win-now vs rebuild advice.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

AGES = np.arange(21, 37)
PRIOR_PAR = 300.0          # points above replacement of pull toward the position's overall rate, per age
TILT = 0.5                 # share of the age gap applied on top of a ranking that already knows age


def _non_increasing(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted pool-adjacent-violators: the closest curve that never goes up."""
    blocks: list[list[float]] = []
    for v, wt in zip(y, w):
        blocks.append([float(v), float(wt), 1])
        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    return np.array([v for v, _, c in blocks for _ in range(int(c))])


def retention_table(seasons: pd.DataFrame, upto: int) -> dict[str, dict[int, np.ndarray]]:
    """seasons: player_id, season, position, par, age. Only pairs with s + k <= upto are used (walk-forward).
    Returns {pos: {k: array over AGES}}."""
    par = seasons.set_index(["player_id", "season"]).par
    out: dict[str, dict[int, np.ndarray]] = {}
    for pos, g in seasons[(seasons.par > 0) & seasons.age.notna()].groupby("position"):
        out[pos] = {}
        for k in (1, 2, 3):
            gg = g[g.season + k <= upto]
            later = np.array([par.get((p, s + k), 0.0) for p, s in zip(gg.player_id, gg.season)])
            now = gg.par.to_numpy()
            age = gg.age.round().clip(AGES[0], AGES[-1]).to_numpy()
            overall = later.sum() / max(now.sum(), 1.0)
            num = np.array([later[age == a].sum() for a in AGES])
            den = np.array([now[age == a].sum() for a in AGES])
            out[pos][k] = _non_increasing((num + PRIOR_PAR * overall) / (den + PRIOR_PAR), den + PRIOR_PAR)
    return out


def retention(table: dict[str, dict[int, np.ndarray]], pos: str, age: float | None, k: int) -> float | None:
    """Share of this season's value expected k seasons from now."""
    if pos not in table or age is None or np.isnan(age) or k not in table[pos]:
        return None
    return float(table[pos][k][int(min(AGES[-1], max(AGES[0], round(age)))) - AGES[0]])


def age_tilt(table: dict[str, dict[int, np.ndarray]], pos: str, age: float | None, k: int) -> float:
    """Multiplier on a rank-based projection k seasons out: >1 for a player young for his position, <1 for old."""
    r = retention(table, pos, age, k)
    if r is None:
        return 1.0
    typical = float(np.median(table[pos][k]))
    return float((r / typical) ** TILT) if typical > 0 else 1.0
