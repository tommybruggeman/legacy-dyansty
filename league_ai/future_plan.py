"""Plan the next few seasons for one team: cap room by season, the core under contract, each upcoming auction's free
agent class with likely prices and who will bid, and the picks this owner holds. No model call.

Everything is the league's own data and math (contracts, the league's settings, fair salaries from this league's cap
hits, the position/age table in roster_moves). Money is in cap dollars:

- worth: a player's fair salary here for his value now (k-th most valuable rostered player ~ k-th biggest cap hit),
  carried into later seasons by how players of his position and age held their value (roster_moves.retention);
- auction k: the auction before season (this season + k). Its class is every contract with k seasons left now, minus
  rookie deals whose option is worth picking up, plus contracts their owners would do better to cut before it;
- likely price: his worth that season, times the market's heat (league-wide room against the class's worth, only
  for the next auction; further out, other auctions refill the room first), times the strongest pay-up habit among
  the rivals likely to bid (from each owner's current non-rookie contracts vs fair salaries);
- suggested years: the contract length (up to the league maximum) with the most surplus (worth each season minus the
  price), ties going to a length that ends in a season where less of this owner's salary comes off the books, so
  deals stay staggered.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Iterable, Mapping

from league_ai.power import FIXED, FLEX
from league_ai.roster_moves import ROOKIE_OPTION, Rules, cut_or_keep, retention

HORIZON = 3               # auctions planned ahead
SPEND_SHARE = 0.85        # share of league-wide room that actually gets spent at an auction
HEAT_RANGE = (0.8, 1.4)
LEAN_SHRINK = 4.0         # contracts of pull toward 1.0 for an owner's pay-up habit at a position
LEAN_WEIGHT = 0.5         # share of the most eager rival's habit that shows up in the price
LEAN_CAP = 1.25
TARGETS_PER_CLASS = 8
OPTION_KEEP_MARGIN = 0.0  # option surplus above this -> the owner keeps him
POS = ("QB", "RB", "WR", "TE")


def _salary(p: Any) -> float:
    return float(getattr(p, "salary", 0) or getattr(p, "cap_hit", 0) or 0)


def _years(p: Any) -> int:
    return max(int(getattr(p, "years_left", 1) or 1), 1)


class Worth:
    """Fair salary k seasons from now. Every rostered player's value is carried forward by the position/age table, the
    league is re-ranked, and the k-th most valuable then gets the k-th biggest cap hit of today's league (the cap
    doesn't shrink as players age: whoever holds value climbs the ranks). Incoming rookies and breakouts aren't in
    the ranking, so far-out worth runs a little high."""

    def __init__(self, players: Iterable[Any], fair: Mapping[str, float]):
        self.fair = {k: float(v) for k, v in fair.items()}
        self.meta = {p.sleeper_id: (p.position, p.age) for p in players if p.sleeper_id in self.fair}
        self.curve = sorted((_salary(p) for p in players if _salary(p) > 0), reverse=True)
        self._by_k: dict[int, dict[str, float]] = {0: self.fair}

    def table(self, k: int) -> dict[str, float]:
        if k not in self._by_k:
            proj = {pid: self.fair[pid] * retention(pos, age, k) for pid, (pos, age) in self.meta.items()}
            order = sorted(proj, key=lambda pid: -proj[pid])
            self._by_k[k] = {pid: (self.curve[i] if i < len(self.curve) else 1.0) for i, pid in enumerate(order)}
        return self._by_k[k]

    def __call__(self, p: Any, k: int) -> float:
        return round(self.table(k).get(p.sleeper_id, 0.0), 2)


def lineup_worth(players: Iterable[Any], slots: list[str], worth: Mapping[str, float]) -> tuple[float, dict[str, float], list[str]]:
    """Best lineup by worth: (total, worth by position, empty or replacement-level slots)."""
    pool = sorted(players, key=lambda p: -worth.get(p.sleeper_id, 0.0))
    used: set[str] = set()
    by_pos: dict[str, float] = defaultdict(float)
    holes: list[str] = []
    ordered = [s for s in slots if s in FIXED] + [s for s in slots if s in FLEX]
    for slot in ordered:
        eligible = {slot} if slot in FIXED else FLEX.get(slot, set())
        pick = next((p for p in pool if p.sleeper_id not in used and p.position in eligible), None)
        if pick is None or worth.get(pick.sleeper_id, 0.0) <= 2.0:
            holes.append(slot)
        if pick is not None:
            used.add(pick.sleeper_id)
            by_pos[pick.position] += worth.get(pick.sleeper_id, 0.0)
    return round(sum(by_pos.values()), 2), dict(by_pos), holes


def pay_up_leans(players: Iterable[Any], fair: Mapping[str, float]) -> dict[str, dict[str, float]]:
    """team -> position -> what the owner pays against fair salary on his current non-rookie deals (shrunk toward 1)."""
    paid: dict[tuple[str, str], list[float]] = defaultdict(list)
    for p in players:
        s, f = _salary(p), float(fair.get(p.sleeper_id, 0.0))
        if s > 1 and f > 1 and getattr(p, "contract_type", None) != "rookie" and p.position in POS:
            paid[(str(p.team_id), p.position)].append(max(0.3, min(3.0, s / f)))
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for (tid, pos), xs in paid.items():
        n = len(xs)
        out[tid][pos] = round(1.0 + (sum(xs) / n - 1.0) * n / (n + LEAN_SHRINK), 2)
    return out


def _option_kept(p: Any, worth: "Worth", rules: Rules) -> float | None:
    """The option salary if this rookie deal's option is worth picking up when it ends (else None)."""
    rnd = int(getattr(p, "rookie_round", 0) or 0)
    if getattr(p, "contract_type", None) != "rookie" or rnd not in ROOKIE_OPTION or rules.option_years <= 0:
        return None
    end = _years(p)   # the option seasons are seasons end .. end + option_years - 1 from now
    surplus = sum(worth(p, end + y) - ROOKIE_OPTION[rnd] for y in range(rules.option_years))
    return ROOKIE_OPTION[rnd] if surplus > OPTION_KEEP_MARGIN else None


def best_length(p: Any, worth: "Worth", k: int, price: float, max_years: int, light_years: Mapping[int, float],
                season: int) -> tuple[int, float]:
    """(years, surplus) for a deal signed at auction k: worth each season minus the price, the best length up to the max.
    light_years: season -> this owner's salary coming off after it; a tie (within $1) goes to the lighter year."""
    options = []
    total = 0.0
    for y in range(1, max(1, max_years) + 1):
        total += worth(p, k + y - 1) - price
        ends = season + k + y - 1
        options.append((round(total, 2), -light_years.get(ends, 0.0), y))
    top = max(o[0] for o in options)
    y = max((o for o in options if o[0] >= top - 1.0), key=lambda o: (o[1], o[0]))[2]
    return y, round(next(o[0] for o in options if o[2] == y), 2)


def plan(*, players: list[Any], teams: Mapping[str, Mapping[str, Any]], slots: list[str], asker: str, season: int, salary_cap: float,
         fair: Mapping[str, float], rules: Rules, dead_future: Mapping[str, Mapping[int, float]] | None = None,
         picks: Iterable[Mapping[str, Any]] | None = None, max_years: int = 4, horizon: int = HORIZON,
         focus: str | None = None) -> dict[str, Any]:
    """The multi-season picture for team `asker`. players: PlayerStrength-like with salary, years_left, contract_type,
    rookie_round, age, position, team_id, owner. dead_future: team -> season -> dead cap already owed that season."""
    asker = str(asker)
    dead_future = dead_future or {}
    by_team: dict[str, list[Any]] = defaultdict(list)
    for p in players:
        by_team[str(p.team_id)].append(p)
    name = {tid: str(t.get("owner_name") or t.get("team_name") or tid) for tid, t in teams.items()}
    leans = pay_up_leans(players, fair)
    W = Worth(players, fair)
    kept: dict[str, float] = {}           # rookie deals whose option is worth picking up -> option salary
    for p in players:
        if _years(p) <= horizon:
            price = _option_kept(p, W, rules)
            if price is not None:
                kept[p.sleeper_id] = price

    def _through(p: Any) -> int:
        return season + _years(p) - 1 + (rules.option_years if p.sleeper_id in kept else 0)

    def under_contract(tid: str, k: int) -> list[Any]:
        """Players still signed for season + k (k-th season from now), rookie options picked up counted in."""
        return [p for p in by_team.get(tid, []) if _years(p) > k or (p.sleeper_id in kept and _years(p) + rules.option_years > k)]

    def committed(tid: str, k: int) -> float:
        return round(sum(kept.get(p.sleeper_id, _salary(p)) if _years(p) <= k else _salary(p) for p in under_contract(tid, k)), 2)

    # salary coming off this owner's books after each season (for staggering)
    light: dict[int, float] = defaultdict(float)
    for p in by_team.get(asker, []):
        light[season + _years(p) - 1] += _salary(p)

    seasons_out, classes = [], []
    for k in range(1, horizon + 1):
        year = season + k
        worth = {p.sleeper_id: W(p, k) for p in players}
        room = {tid: round(float(salary_cap) - committed(tid, k) - float(dead_future.get(tid, {}).get(year, 0.0)), 2) for tid in teams}
        lineups = {tid: lineup_worth(under_contract(tid, k), slots, worth) for tid in teams}
        med_pos = {pos: median([lineups[t][1].get(pos, 0.0) for t in teams]) for pos in POS} if teams else {}
        needs = {tid: [pos for pos in POS if lineups[tid][1].get(pos, 0.0) < 0.8 * med_pos.get(pos, 0.0)] for tid in teams}
        strength = {tid: lineups[tid][0] + 0.9 * max(room[tid], 0.0) for tid in teams}

        def rank(d: Mapping[str, float], tid: str) -> int:
            return sorted(d, key=lambda t: -d[t]).index(tid) + 1

        mine = under_contract(asker, k)
        core = sorted(mine, key=lambda p: -worth.get(p.sleeper_id, 0.0))[:6]
        seasons_out.append({
            "season": year,
            "committed": committed(asker, k),
            "dead_cap": round(float(dead_future.get(asker, {}).get(year, 0.0)), 2),
            "room": room.get(asker, 0.0),
            "room_rank": f"{rank(room, asker)} of {len(teams)}" if asker in room else None,
            "signed_core_rank": f"{rank({t: v[0] for t, v in lineups.items()}, asker)} of {len(teams)}" if asker in lineups else None,
            "core_plus_room_rank": f"{rank(strength, asker)} of {len(teams)}" if asker in strength else None,
            "holes": lineups.get(asker, (0, {}, []))[2],
            "thin_positions": needs.get(asker, []),
            "core": [f"{p.name} {p.position}, age {round(p.age + k) if p.age else '?'}, ${kept.get(p.sleeper_id, _salary(p)):g}, "
                     f"worth ~${worth.get(p.sleeper_id, 0.0):.0f}, signed through {_through(p)}" for p in core],
            "salary_coming_off_after": round(light.get(year, 0.0), 2),
        })

        # the class: contracts ending after season + k - 1, minus kept rookie options, plus likely cuts before this auction
        pool = [p for p in players if _years(p) == k and p.sleeper_id not in kept and p.position in POS]
        casualties: list[Any] = []
        if k == 1:
            for p in players:
                s = _salary(p)
                if str(p.team_id) == asker or s <= 1 or _years(p) < 2 or getattr(p, "contract_type", None) == "rookie" or p.sleeper_id not in fair:
                    continue
                r = cut_or_keep(name=p.name, pos=p.position, age=p.age, salary=s, years_left=_years(p) - 1, fair_now=W(p, 1),
                                rules=rules, offseason=True)
                if r["call"] == "cut now" and W(p, 1) >= 3:
                    casualties.append(p)
        cut_ids = {p.sleeper_id for p in casualties}
        heat = 1.0
        if k == 1:
            pool_worth = sum(W(p, k) for p in pool + casualties)
            spend = SPEND_SHARE * sum(max(v, 0.0) for v in room.values())
            if pool_worth > 0:
                heat = round(max(HEAT_RANGE[0], min(HEAT_RANGE[1], spend / pool_worth)), 2)
        bidders = sorted((t for t in teams if t != asker), key=lambda t: -room[t])[:4]
        rows = []
        for p in pool + casualties:
            w = W(p, k)
            if w < 2.0:
                continue
            rivals = [t for t in bidders if p.position in needs.get(t, []) and room[t] >= w]
            lean = max([leans.get(t, {}).get(p.position, 1.0) for t in rivals] or [1.0])
            push = min(LEAN_CAP, 1.0 + LEAN_WEIGHT * max(0.0, lean - 1.0))   # an eager rival bids him up, partly
            price = round(max(1.0, w * heat * push), 0)
            years, surplus = best_length(p, W, k, price, max_years, light, season)
            fit = []
            if p.position in needs.get(asker, []) or any(h == p.position for h in lineups.get(asker, (0, {}, []))[2]):
                fit.append("fills a thin spot")
            if W(p, k + 1) >= 0.9 * w:
                fit.append("holds value")
            score = w * (1.35 if fit and fit[0] == "fills a thin spot" else 1.0) + 0.5 * max(surplus, 0.0)
            if focus and p.position != focus.upper():
                continue
            rows.append((score, {
                "player": p.name, "pos": p.position, "age_then": round(p.age + k) if p.age else None,
                "now_with": name.get(str(p.team_id), "?") if str(p.team_id) != asker else "you (his deal runs out)",
                "salary_now": _salary(p), "worth_then": round(w), "likely_price": int(price),
                "suggested_years": years, "surplus_over_deal": round(surplus),
                "why": ", ".join(fit) or None,
                "may_be_cut_first": True if p.sleeper_id in cut_ids else None,
                "likely_rivals": [f"{name[t]} (${room[t]:.0f} room)" for t in rivals[:2]] or None,
            }))
        rows.sort(key=lambda r: -r[0])
        option_keeps = [f"{p.name} ({name.get(str(p.team_id), '?')}, ${kept[p.sleeper_id]:g} option)" for p in players
                        if p.sleeper_id in kept and _years(p) == k and W(p, k) >= 5]
        classes.append({
            "auction": f"before the {year} season",
            "market_heat": heat if k == 1 else "too far out to price (earlier auctions refill the room)",
            "rivals_with_most_room": [{"owner": name[t], "room": room[t], "needs": needs.get(t, []),
                                       "pays_up_for": [f"{pos} (+{round((v - 1) * 100)}%)" for pos, v in leans.get(t, {}).items() if v >= 1.12] or None}
                                      for t in bidders],
            "targets": [r[1] for r in rows[:TARGETS_PER_CLASS]],
            "likely_kept_on_rookie_option": option_keeps[:6] or None,
            "class_size": len(pool) + len(casualties),
        })

    my_picks = []
    for r in picks or []:
        if str(r.get("current_owner_league_team_id")) == asker and r.get("asset_status", "tradable") == "tradable" and int(r.get("draft_year") or 0) > season - 1:
            via = str(r.get("original_owner_name") or "")
            own = str(r.get("original_league_team_id")) == asker
            my_picks.append(f"{r.get('draft_year')} R{r.get('round_number')}" + ("" if own else f" (via {via})"))
    gone = []
    for r in picks or []:
        if str(r.get("original_league_team_id")) == asker and str(r.get("current_owner_league_team_id")) != asker \
                and r.get("asset_status", "tradable") == "tradable":
            gone.append(f"{r.get('draft_year')} R{r.get('round_number')} (owned by {r.get('current_owner_name') or '?'})")

    window = [s["season"] for s in seasons_out if s["core_plus_room_rank"] and int(s["core_plus_room_rank"].split(" ")[0]) <= 4]
    return {
        "seasons": seasons_out,
        "free_agent_classes": classes,
        "your_picks": sorted(my_picks),
        "your_picks_traded_away": sorted(gone) or None,
        "contending_window": (f"core plus room ranks top 4 in {', '.join(map(str, window))}" if window
                              else "core plus room doesn't rank top 4 in any of the next seasons yet"),
        "rules": {"max_contract_years": max_years, "dead_cap_pct": rules.dead_cap_pct, "rookie_option_years": rules.option_years,
                  "taxi_skips_contract_year": rules.taxi_skips_year},
        "how_to_use": ("Seasons: room = cap minus salaries still signed and dead cap owed; signed_core_rank ranks the best lineup of players "
                       "still under contract by worth (fair salary, aged by position); core_plus_room_rank adds what the room buys. "
                       "Targets are this owner's best fits from each auction's class, with the likely price (worth x market heat x the "
                       "most eager rival's habit) and the length with the most surplus that keeps expirations staggered. Further-out "
                       "classes are less certain (trades, cuts, extensions). Name specific players and prices; build the plan season by season."),
    }
