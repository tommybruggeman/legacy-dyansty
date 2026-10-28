"""What a rookie pick is worth, on the same superflex trade-value scale as players.

Two views, blended half and half:
- the market: DynastyProcess pick prices (what other owners will pay for it today);
- what picks really became: the draft study (league_ai/draft_study.py, 1,000 simulated drafts of every 2020-2024 class)
  scored each slot by the trade value of the player it landed three seasons later.

The market prices the top of round 1 above what those picks turned into and prices late 1sts, 2nds and 3rds far below
it, so the blend keeps 1.01 clearly ahead of 1.02 while not treating a 2nd as worthless.

A draft whose class looks deep or thin (its rookie rankings, once they exist) moves every pick in it: `class_strength`
is that class's top-30 ranked value against an average class, and half the gap is applied. In 2020-2025 the
realized value of a class's top 30 picks ran from about 0.6x to 1.3x an average class (2022 thinnest), and the
board saw only part of that coming, hence half.

A pick with no slot yet ("2027 R1") takes the original owner's slot from this season's record (worst record picks
first), leaning on the round average early in the season, and the round average when the owner isn't known.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

# Trade value (superflex market scale) of the k-th rookie taken, three seasons later. 10-team superflex, 3 rounds,
# FantasyPros rookie rankings as of each May, 1,000 drafts per class, classes 2020-2024 (run of 2026-10-28).
REALIZED = [4575, 4988, 4394, 3757, 3346, 2934, 2626, 2272, 2166, 2070, 1712, 1551, 1454, 1373, 1367,
            1245, 1297, 1252, 1180, 1051, 984, 898, 748, 710, 688, 687, 645, 621, 572, 511]
# Market price of the k-th rookie pick (DynastyProcess "Pick r.ss", superflex), used when the database has no slot rows.
MARKET = [7977, 6453, 5239, 4268, 3489, 2862, 2356, 1946, 1613, 1341, 1119, 937, 787, 663, 560,
          475, 405, 345, 296, 254, 219, 189, 164, 143, 124, 109, 95, 84, 74, 66, 58, 51, 45, 40, 35, 31]
MARKET_WEIGHT = 0.5
FUTURE_DISCOUNT = 0.8      # per year out, when the market has no price for that draft
CLASS_TRUST = 0.5          # share of a class's ranked depth (vs an average class) applied to its picks
FULL_SEASON_GAMES = 14     # by then a record says where the pick lands
ROUND_WORDS = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th"}

_ROUND = re.compile(r"\b(?:r|rd|round)\s*(\d)\b|\b(\d)(?:st|nd|rd|th)\b")
_SLOT = re.compile(r"\b(\d)\.(\d{1,2})\b")
_YEAR = re.compile(r"\b(20\d{2})\b")
_VIA = re.compile(r"\(via ([^)]+)\)", re.I)


def non_increasing(values: list[float]) -> list[float]:
    """Pool-adjacent-violators: a later pick is never worth more than an earlier one (sample noise put 1.02 above 1.01)."""
    blocks = [[float(v), 1] for v in values]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0]:
            n = blocks[i][1] + blocks[i + 1][1]
            blocks[i:i + 2] = [[(blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / n, n]]
            i = max(i - 1, 0)
        else:
            i += 1
    return [v for v, n in blocks for _ in range(n)]


def class_strength(ranked_values: Mapping[int, list[float]]) -> dict[int, float]:
    """{draft year: trade values of its ranked rookies} -> each class's top-30 total against the average class."""
    totals = {y: sum(sorted(v, reverse=True)[:30]) for y, v in ranked_values.items() if v}
    if not totals:
        return {}
    avg = sum(totals.values()) / len(totals)
    return {y: t / avg for y, t in totals.items()} if avg else {}


def parse(label: str) -> dict[str, Any]:
    """'2027 R1', '2027 1st', '2027 Round 2', '2027 Early 1st', '2027 1.03', '2027 Pick 1.03 (via Hardy)' -> year, round, slot, tier, via."""
    text = str(label or "")
    low = text.lower()
    for word, short in ROUND_WORDS.items():
        low = low.replace(word, short)
    out: dict[str, Any] = {"label": text, "year": None, "round": None, "slot": None, "tier": None, "via": None}
    if m := _YEAR.search(low):
        out["year"] = int(m.group(1))
        low = low.replace(m.group(1), " ")
    if m := _VIA.search(text):
        out["via"] = m.group(1).strip()
        low = _VIA.sub(" ", low)
    if m := _SLOT.search(low):
        out["round"], out["slot"] = int(m.group(1)), int(m.group(2))
    elif m := _ROUND.search(low):
        out["round"] = int(m.group(1) or m.group(2))
    for tier in ("early", "mid", "late"):
        if tier in low:
            out["tier"] = tier
    return out


class PickBook:
    """Pick values for one league: slot curve x each draft year's market level."""

    def __init__(self, market_rows: list[Mapping[str, Any]] | None, teams: int, rounds: int, season: int,
                 records: Mapping[str, tuple[int, int]] | None = None, market_weight: float = MARKET_WEIGHT,
                 class_strength: Mapping[int, float] | None = None):
        self.teams, self.rounds, self.season = max(int(teams or 10), 2), max(int(rounds or 3), 1), int(season)
        self.records = dict(records or {})
        self.class_strength = {int(y): float(v) for y, v in (class_strength or {}).items() if v}
        rows = [r for r in market_rows or [] if r.get("value_2qb")]
        slots = sorted(((int(str(r["slot"]).split(".")[0]), int(str(r["slot"]).split(".")[1]), float(r["value_2qb"]))
                        for r in rows if r.get("slot") and int(r.get("draft_year") or 0) == min((int(x.get("draft_year") or 9999) for x in rows if x.get("slot")), default=0)))
        dp_teams = max((s for _, s, _ in slots), default=12) or 12
        market = [v for _, _, v in slots] or list(MARKET)
        n = self.teams * self.rounds
        market = (market + [market[-1]] * n)[:n]
        realized = list(REALIZED) + [REALIZED[-1] * (0.9 ** (i + 1)) for i in range(max(0, n - len(REALIZED)))]
        realized = non_increasing(realized[:n])
        self.curve = [market_weight * m + (1 - market_weight) * r for m, r in zip(market, realized)]
        # each year's market level: its Mid 1st against the slot curve's middle-of-round-1 price (12-team market board)
        mid = market[min(dp_teams // 2, len(market) - 1) - 1] if market else 1.0
        self.year_factor: dict[int, float] = {}
        for r in rows:
            if str(r.get("pick_label") or "").lower().endswith("mid 1st") and r.get("draft_year"):
                self.year_factor[int(r["draft_year"])] = float(r["value_2qb"]) / mid if mid else 1.0

    def factor(self, year: int | None) -> float:
        if year is None:
            year = self.season + 1
        depth = 1 + CLASS_TRUST * (self.class_strength[year] - 1) if year in self.class_strength else 1.0
        return depth * self._market_factor(year)

    def _market_factor(self, year: int) -> float:
        if year in self.year_factor:
            return self.year_factor[year]
        known = sorted(self.year_factor)
        if known:
            base = known[-1] if year > known[-1] else known[0]
            return self.year_factor[base] * FUTURE_DISCOUNT ** max(0, year - base)
        return FUTURE_DISCOUNT ** max(0, year - (self.season + 1))

    def slot_value(self, year: int | None, rnd: int, slot: int) -> float:
        k = (rnd - 1) * self.teams + (slot - 1)
        k = min(max(k, 0), len(self.curve) - 1)
        return self.curve[k] * self.factor(year)

    def round_value(self, year: int | None, rnd: int, tier: str | None = None) -> float:
        lo, hi = (rnd - 1) * self.teams, min(rnd * self.teams, len(self.curve))
        if lo >= len(self.curve):
            return self.curve[-1] * self.factor(year)
        span = self.curve[lo:hi]
        if tier:
            third = max(len(span) // 3, 1)
            span = {"early": span[:third], "mid": span[third:len(span) - third] or span, "late": span[-third:]}[tier]
        return sum(span) / len(span) * self.factor(year)

    def projected_slot(self, team_id: str | None) -> tuple[int | None, float]:
        """Where this team's pick lands if the season ended today (worst record first) and how much to trust it (0-1)."""
        if not team_id or team_id not in self.records or len(self.records) < self.teams // 2:
            return None, 0.0
        pct = {tid: (w / (w + l) if (w + l) else 0.5) for tid, (w, l) in self.records.items()}
        order = sorted(pct, key=lambda t: pct[t])
        games = max((w + l for w, l in self.records.values()), default=0)
        return order.index(team_id) + 1, min(1.0, games / FULL_SEASON_GAMES)

    def value(self, label: str, team_id: str | None = None) -> tuple[float, str]:
        """(value, how it was priced) for a pick label; team_id is the pick's original owner when the label has no 'via'."""
        p = parse(label)
        rnd = p["round"]
        if not rnd:
            return 0.0, "no round in the label"
        if p["slot"]:
            return self.slot_value(p["year"], rnd, p["slot"]), f"slot {rnd}.{p['slot']:02d}"
        if p["tier"]:
            return self.round_value(p["year"], rnd, p["tier"]), f"{p['tier']} round {rnd}"
        avg = self.round_value(p["year"], rnd)
        if (p["year"] or self.season + 1) == self.season + 1:
            slot, trust = self.projected_slot(team_id)
            if slot:
                v = trust * self.slot_value(p["year"], rnd, slot) + (1 - trust) * avg
                return v, f"projected {rnd}.{slot:02d} from the original owner's record ({round(trust * 100)}% weight)"
        return avg, f"round {rnd} average"

    def labels(self) -> list[tuple[str, float]]:
        """Round and early/mid/late prices for the next two drafts, for 'market fair would add a ___'."""
        out = []
        for year in (self.season + 1, self.season + 2):
            for rnd in range(1, self.rounds + 1):
                suffix = {1: "1st", 2: "2nd", 3: "3rd"}.get(rnd, f"{rnd}th")
                out.append((f"{year} {suffix}", self.round_value(year, rnd)))
                for tier in ("early", "mid", "late"):
                    out.append((f"{year} {tier.title()} {suffix}", self.round_value(year, rnd, tier)))
        return out
