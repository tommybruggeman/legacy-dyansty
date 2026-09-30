"""Power rankings and lineup strength, computed from data (no model call).

now_score   : best legal lineup by recent production, season production and market tier
dynasty     : same lineup logic but valued by market tier, age and contract years
Also yields each team's weakest starting position for the Front Office tiles.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Mapping

FLEX = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"}, "REC_FLEX": {"WR", "TE"}, "IDP_FLEX": set()}
FIXED = {"QB", "RB", "WR", "TE", "K", "DEF"}
SLOT_LABEL = {"SUPER_FLEX": "OP", "FLEX": "FLEX", "REC_FLEX": "REC FLEX", "DEF": "DST"}

# positional rank -> points-per-game-like scale, so tiers are comparable to production
TIER_CURVE = {"QB": (26.0, 30, 8.0), "RB": (22.0, 48, 5.0), "WR": (21.0, 60, 5.0), "TE": (16.0, 24, 4.0), "K": (9.0, 24, 5.0), "DEF": (9.0, 24, 4.0)}
PEAK_AGE = {"QB": 30, "RB": 25, "WR": 27, "TE": 28, "K": 32, "DEF": 30}
AGE_DECAY = {"QB": 0.03, "RB": 0.09, "WR": 0.06, "TE": 0.06, "K": 0.02, "DEF": 0.0}


def tier_points(position: str | None, pos_rank: float | None) -> float | None:
    if position not in TIER_CURVE or pos_rank is None:
        return None
    top, span, floor = TIER_CURVE[position]
    r = max(1.0, float(pos_rank))
    if r >= span:
        return floor
    return top - (top - floor) * (r - 1) / (span - 1)


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


@dataclass
class PlayerStrength:
    sleeper_id: str
    name: str
    position: str
    team_id: str
    owner: str
    now: float
    dynasty: float
    available: bool
    recent_ppg: float | None = None
    season_ppg: float | None = None
    pos_rank: float | None = None
    age: float | None = None
    years_left: int = 1
    injury: str | None = None


@dataclass
class TeamPower:
    team_id: str
    owner: str
    now_score: float
    dynasty_score: float
    now_rank: int = 0
    dynasty_rank: int = 0
    starters: list[PlayerStrength] = field(default_factory=list)
    slot_scores: dict[str, float] = field(default_factory=dict)
    weakest_slot: str | None = None
    weakest_note: str | None = None
    strongest_slot: str | None = None


def player_strength(row: Mapping[str, Any], *, recent_ppg: float | None, season_ppg: float | None, pos_rank: float | None, age: float | None, injury_status: str | None) -> PlayerStrength:
    pos = str(row.get("pos") or row.get("position") or "").upper()
    tier = tier_points(pos, pos_rank)
    parts, weights = [], []
    if recent_ppg is not None:
        parts.append(recent_ppg); weights.append(0.5)
    if season_ppg is not None:
        parts.append(season_ppg); weights.append(0.3)
    if tier is not None:
        parts.append(tier); weights.append(0.2 if parts[:-1] else 1.0)
    now = sum(p * w for p, w in zip(parts, weights)) / sum(weights) if weights else 0.0
    status = (injury_status or "").lower()
    available = status not in {"out", "ir", "injured reserve", "pup", "suspended", "doubtful"} and row.get("roster_designation") != "ir"
    if status == "questionable":
        now *= 0.93
    years = int(row.get("contract_years_left") or 1)
    age_pen = 1.0
    if age is not None and pos in PEAK_AGE:
        over = max(0.0, float(age) - PEAK_AGE[pos])
        age_pen = max(0.4, 1.0 - AGE_DECAY[pos] * over)
        if float(age) < PEAK_AGE[pos] - 3:
            age_pen *= 1.05
    dyn_base = tier if tier is not None else now
    dynasty = dyn_base * age_pen * (1.0 + 0.04 * min(years, 3))
    return PlayerStrength(
        sleeper_id=str(row.get("sleeper_player_id") or row.get("player_id") or ""), name=str(row.get("player_name") or ""), position=pos,
        team_id=str(row.get("league_team_id") or ""), owner=str(row.get("owner_name") or ""), now=round(now, 2), dynasty=round(dynasty, 2),
        available=available, recent_ppg=recent_ppg, season_ppg=season_ppg, pos_rank=pos_rank, age=age, years_left=years, injury=injury_status,
    )


def fill_lineup(players: Iterable[PlayerStrength], slots: list[str], key: str = "now") -> tuple[list[tuple[str, PlayerStrength]], list[PlayerStrength]]:
    pool = sorted([p for p in players if (key != "now" or p.available)], key=lambda p: getattr(p, key), reverse=True)
    used: set[str] = set()
    lineup: list[tuple[str, PlayerStrength]] = []
    ordered = [s for s in slots if s in FIXED] + [s for s in slots if s in FLEX]
    for slot in ordered:
        eligible = {slot} if slot in FIXED else FLEX.get(slot, set())
        pick = next((p for p in pool if p.sleeper_id not in used and p.position in eligible), None)
        if pick:
            used.add(pick.sleeper_id)
            lineup.append((slot, pick))
    bench = [p for p in pool if p.sleeper_id not in used]
    return lineup, bench


def compute_power(players: list[PlayerStrength], teams: list[Mapping[str, Any]], roster_positions: list[str], actual_ppg: Mapping[str, float] | None = None) -> list[TeamPower]:
    """actual_ppg: owner name -> points per game this season in league scoring (blended 55/45 with the roster projection when present)."""
    actual_ppg = {k.casefold(): v for k, v in (actual_ppg or {}).items()}
    slots = [s for s in roster_positions if s not in {"BN", "IR", "TAXI"}] or ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]
    by_team: dict[str, list[PlayerStrength]] = defaultdict(list)
    for p in players:
        by_team[p.team_id].append(p)
    out: list[TeamPower] = []
    for t in teams:
        tid = str(t.get("league_team_id"))
        roster = by_team.get(tid, [])
        lineup, bench = fill_lineup(roster, slots, "now")
        starter_total = sum(p.now for _, p in lineup)
        depth = 0.0
        for pos in ("QB", "RB", "WR", "TE"):
            nxt = next((p.now for p in bench if p.position == pos), 0.0)
            depth += nxt
        projection = starter_total * 0.85 + depth * 0.15
        actual = actual_ppg.get(str(t.get('owner_name') or '').casefold())
        now_score = 0.55 * actual + 0.45 * projection if actual else projection
        d_lineup, d_bench = fill_lineup(roster, slots, "dynasty")
        d_total = sum(p.dynasty for _, p in d_lineup)
        d_depth = sum(sorted((p.dynasty for p in d_bench), reverse=True)[:4])
        dynasty_score = d_total * 0.8 + d_depth * 0.2
        slot_scores: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for slot, p in lineup:
            grp = SLOT_LABEL.get(slot, slot)
            slot_scores[grp] += p.now
            counts[grp] += 1
        avg_slots = {g: slot_scores[g] / counts[g] for g in slot_scores}
        out.append(TeamPower(team_id=tid, owner=str(t.get("owner_name") or ""), now_score=round(now_score, 1), dynasty_score=round(dynasty_score, 1),
                             starters=[p for _, p in lineup], slot_scores=avg_slots))
    # ranks
    for i, tp in enumerate(sorted(out, key=lambda x: -x.now_score), start=1):
        tp.now_rank = i
    for i, tp in enumerate(sorted(out, key=lambda x: -x.dynasty_score), start=1):
        tp.dynasty_rank = i
    # weakest / strongest slot relative to league median for that slot group
    groups = {g for tp in out for g in tp.slot_scores}
    medians = {g: median([tp.slot_scores.get(g, 0.0) for tp in out]) for g in groups}
    for tp in out:
        gaps = {g: tp.slot_scores.get(g, 0.0) - medians[g] for g in groups if medians[g] > 0}
        if gaps:
            weakest = min(gaps, key=gaps.get)
            strongest = max(gaps, key=gaps.get)
            tp.weakest_slot = weakest
            tp.strongest_slot = strongest
            pct = gaps[weakest] / medians[weakest] * 100 if medians[weakest] else 0
            tp.weakest_note = f"{pct:+.0f}% vs league median" if pct < 0 else "at or above league median everywhere"
    return out
