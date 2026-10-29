"""What a player is worth to a particular team. No model call.

The dynasty market prices a player once, for an average team. A contender and a rebuilder value the
same player very differently: a 32-year-old RB scoring 23 a game is worth little on the market and a
lot to a team trying to win this year. So every player gets three numbers on the market's own scale:

  future   : the dynasty market value (DynastyProcess, superflex), which already prices age
  now      : what the market pays for a player producing like this one does this season, i.e. the
             market value of the player who holds the same positional rank by market that this
             player holds by current production (the RB2 by points is valued like the RB2 by market)
  contract : cap surplus or cost. A $21 x 2yr bench WR costs a cap team real room.

Cap space itself trades too (sent as FAAB in Sleeper; the sender loses that much cap, the receiver gains it).
A dollar of cap is priced at a share of what a dollar buys on this league's rosters, since room only turns
into points once it is spent on someone.

A player coming off a major or moderate injury gets a note on what that injury did to similar players' next seasons
(league_ai.injuries). Tilting his value by it is off (INJURY_TILT): on this league's completed trades it made the
engine fit what owners actually agreed to slightly worse, so it informs the owner without moving the price.

Each team's window (contending / middle / rebuilding) sets how much now and future count for it.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from league_ai.power import PlayerStrength

WINDOWS = ("contending", "middle", "rebuilding")
NOW_WEIGHT = {"contending": 0.8, "middle": 0.5, "rebuilding": 0.2}
INJURY_TILT = False    # multiply the market part by the injury history effect
CONTRACT_WEIGHT = 0.5  # share of a contract's cap surplus or cost counted in the player's value
CAP_DOLLAR_SHARE = 0.35  # a dollar of cap space is worth this share of the value a rostered dollar buys
POSITIONS = ("QB", "RB", "WR", "TE")


def market_value(p: PlayerStrength) -> float:
    """The dynasty market value on the superflex scale. Falls back to the tier score when no market row exists."""
    v = getattr(p, "market_value", None)
    if v is not None:
        return float(v)
    return max(0.0, float(p.dynasty)) * 400.0


class Valuer:
    def __init__(self, players: Iterable[PlayerStrength], windows: Mapping[str, str]):
        self.players = list(players)
        self.windows = dict(windows)
        by_pos: dict[str, list[PlayerStrength]] = defaultdict(list)
        for p in self.players:
            by_pos[p.position].append(p)
        self._now: dict[str, float] = {}
        self.now_rank: dict[str, int] = {}
        self.market_rank: dict[str, int] = {}
        for pos, ps in by_pos.items():
            market_curve = sorted((market_value(p) for p in ps), reverse=True)
            by_output = sorted(ps, key=lambda p: -(p.now if p.available else p.now * 0.3))
            for i, p in enumerate(by_output):
                self._now[p.sleeper_id] = market_curve[i]
                self.now_rank[p.sleeper_id] = i + 1
            for i, p in enumerate(sorted(ps, key=lambda p: -market_value(p))):
                self.market_rank[p.sleeper_id] = i + 1
        # fair salary by value rank: the k-th most valuable rostered player should cost about the k-th biggest cap hit
        capped = [p for p in self.players if _cap(p) > 0]
        hits = sorted((_cap(p) for p in capped), reverse=True)
        self._fair_cap = {p.sleeper_id: hits[i] for i, p in enumerate(sorted(capped, key=lambda p: -self.base(p, "middle")))}
        total_cap = sum(hits)
        self.cap_dollar = CAP_DOLLAR_SHARE * sum(self.base(p, "middle") for p in capped) / total_cap if total_cap else 0.0

    def now(self, p: PlayerStrength) -> float:
        """This-season value, nudged by the rest-of-season schedule (playoff weeks weighted double) when it's known."""
        return self._now.get(p.sleeper_id, market_value(p)) * float(getattr(p, "schedule_mult", 1.0) or 1.0)

    def base(self, p: PlayerStrength, window: str) -> float:
        w = NOW_WEIGHT.get(window, 0.5)
        return w * self.now(p) + (1 - w) * market_value(p) * (float(getattr(p, "injury_mult", 1.0) or 1.0) if INJURY_TILT else 1.0)

    def contract(self, p: PlayerStrength, window: str) -> float:
        """Cap surplus (+) or cost (-) on the value scale: how far the cap hit sits from what players of this value cost
        in this league, over the years left (up to 3). Never more than half the player's value either way."""
        cap, fair = _cap(p), self._fair_cap.get(p.sleeper_id)
        if cap <= 0 or not fair:
            return 0.0
        base = self.base(p, window)
        gap = max(-1.0, min(1.0, (fair - cap) / max(fair, cap)))
        return CONTRACT_WEIGHT * gap * min(int(p.years_left or 1), 3) / 3 * base

    def value_to(self, team_id: str, p: PlayerStrength) -> float:
        window = self.windows.get(str(team_id), "middle")
        return self.base(p, window) + self.contract(p, window)

    def package(self, team_id: str, ps: Iterable[PlayerStrength], picks: float = 0.0, cap: float = 0.0) -> float:
        """Total value of players (plus pick value on the market scale, plus cap dollars) to one team. Rebuilders value
        picks fully, contenders at half, since picks don't score this year."""
        window = self.windows.get(str(team_id), "middle")
        pick_w = {"contending": 0.5, "middle": 0.8, "rebuilding": 1.0}.get(window, 0.8)
        return sum(self.value_to(team_id, p) for p in ps) + picks * pick_w + self.cap_value(cap)

    def cap_value(self, dollars: float) -> float:
        """What this many dollars of cap space is worth on the value scale."""
        return max(0.0, float(dollars or 0.0)) * self.cap_dollar

    def explain(self, team_id: str, p: PlayerStrength) -> str:
        window = self.windows.get(str(team_id), "middle")
        c, base = self.contract(p, window), self.base(p, window)
        contract = (" contract is a bargain" if c > 0 else " contract costs more than he returns") if base and abs(c) >= 0.15 * base else ""
        ranks = f"{p.position}{self.now_rank.get(p.sleeper_id, '?')} by this season's production, {p.position}{self.market_rank.get(p.sleeper_id, '?')} on the dynasty market"
        injury = getattr(p, "injury_note", None)
        return f"{p.name}: {ranks} (rostered players){';' + contract if contract else ''}{'; injury history ' + injury if injury else ''}"


def team_windows(players: Iterable[PlayerStrength], teams: Iterable[Mapping[str, Any]], now_rank: Mapping[str, int], dynasty_rank: Mapping[str, int],
                 overrides: Mapping[str, str] | None = None, records: Mapping[str, tuple[int, int]] | None = None,
                 leanings: Mapping[str, str] | None = None, playoff_odds: Mapping[str, float] | None = None) -> dict[str, str]:
    """Each team's window from its lineup ranks, record, roster age and trading history (leanings). overrides (team id or owner name -> window)
    come from what owners have said ('Grady is rebuilding') and always win.

    playoff_odds (owner name -> 0-1, from the season simulation) replace the lineup-rank and record terms when present: they already
    combine both, plus the schedule left, and backtest better than either."""
    odds = {str(k).casefold(): float(v) for k, v in (playoff_odds or {}).items()}
    teams = list(teams)
    n = len(teams) or 1
    third = max(2, round(n / 3))
    by_team: dict[str, list[PlayerStrength]] = defaultdict(list)
    for p in players:
        by_team[p.team_id].append(p)
    over = {str(k).casefold(): v for k, v in (overrides or {}).items() if v in WINDOWS}
    out: dict[str, str] = {}
    for t in teams:
        tid = str(t["league_team_id"])
        named = over.get(tid.casefold()) or over.get(str(t.get("owner_name") or "").casefold()) or over.get(str(t.get("team_name") or "").casefold())
        if not named:
            for key, v in over.items():  # first-name match: "Grady" for "Grady Smith"
                if key and key in str(t.get("owner_name") or "").casefold():
                    named = v
                    break
        if named:
            out[tid] = named
            continue
        nr, dr = now_rank.get(tid, n), dynasty_rank.get(tid, n)
        score = 0.0  # + leans contending, - leans rebuilding
        p = odds.get(str(t.get("owner_name") or "").casefold())
        if p is not None:
            score += 1.5 if p >= 0.6 else 0.75 if p >= 0.45 else -1.5 if p <= 0.15 else -0.75 if p <= 0.3 else 0.0
        else:
            score += 0.75 if nr <= third else -0.75 if nr > n - third else 0.0
            w, l = (records or {}).get(tid, (0, 0))
            if w + l >= 4:
                pct = w / (w + l)
                score += 0.75 if pct >= 0.6 else -0.75 if pct <= 0.35 else 0.0
        score += 0.5 if dr - nr >= 3 else -0.5 if nr - dr >= 3 else 0.0
        ages = sorted((p.age for p in by_team.get(tid, []) if p.age is not None and p.position in POSITIONS), reverse=True)[:12]
        if ages:
            avg = sum(ages) / len(ages)
            score += 0.25 if avg >= 27.5 else -0.25 if avg <= 24.5 else 0.0
        lean = (leanings or {}).get(str(t.get("owner_name") or ""))  # how this owner has actually traded (backtest)
        score += 0.5 if lean == "contending" else -0.5 if lean == "rebuilding" else 0.0
        out[tid] = "contending" if score >= 1.0 else "rebuilding" if score <= -1.0 else "middle"
    return out


def nearest_pick(gap: float, pick_values: list[tuple[str, float]]) -> str | None:
    """The pick whose market value is closest to a value gap, to say what 'market fair' would add."""
    if gap <= 0 or not pick_values:
        return None
    label, value = min(pick_values, key=lambda x: abs(x[1] - gap))
    return label if value >= 0.4 * gap else None


def _cap(p: PlayerStrength) -> float:
    return float(getattr(p, "cap_hit", 0.0) or 0.0)
