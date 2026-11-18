"""What a trade does for each team, and whether that explains the trades owners here actually made. No model call.

Plain dynasty value is a poor judge of trades in this league: real accepted trades are lopsided on the market (the short
side's median share is about half), and owners don't price cap into deals. Owners trade to fix something. Two teams that
each swap from a deep room into a hole both start better players afterwards (an RB-for-QB swap where both rooms improve),
and a rebuilder that sends starters away for picks and cap room is doing exactly what a rebuilder should.

So each side of a trade is read on its own terms:

  lineup_gain : points a week the side's best starting lineup gains (its roster just before the trade, then after)
  future      : log of the dynasty market value it gets over what it gives (extra bodies at DEPTH_SHARE, picks priced
                by direction: a rebuilder counts them in full)
  cap_freed   : this season's salary sent away minus salary taken on (plus cap space received)

and one number per side, weighted by the side's direction (contending / middle / rebuilding, from its record then):

  utility = L * now_w * lineup_gain + F * (1 - now_w) * future + C * cap_freed / 10

A trade "makes sense" when both sides' utility is high: score = min(u_a, u_b) + lam * (u_a + u_b).

The test (free, every completed trade): each real trade is ranked against decoys built at the same moment between the
same two owners, the same number of players each way, drawn from their real rosters. If a score understands why
owners trade, the real trade should beat most decoys (AUC; 0.5 = no better than chance). Two decoy kinds: any players
from the rosters, and players of about the same market value (where plain value can't tell them apart by design).
The weights (L, C, F, lam) are fitted on the league's own trades, leave-one-out graded, and refitted every week by the
learning job (league_ai_experiments, experiment 'trade_fit_v1'). The trade finder reads the latest fit.

Prints counts and rates only (the repo is public).
"""
from __future__ import annotations

import math
import random
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from itertools import product
from typing import Any, Callable, Iterable, Mapping

from league_ai.power import FIXED, FLEX

EXPERIMENT = "trade_fit_v1"
DEFAULT_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]
NOW_W = {"contending": 0.8, "middle": 0.5, "rebuilding": 0.2}
PICK_W = {"contending": 0.5, "middle": 0.8, "rebuilding": 1.0}
DEPTH = 0.6
DECOYS = 30
GRID_L = (0.0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5)
GRID_C = (0.0, 0.1, 0.25, 0.5)
GRID_F = (0.0, 1.0)
GRID_LAM = (0.0, 0.25, 0.5, 1.0)
DEFAULT_WEIGHTS = {"L": 0.1, "C": 0.1, "F": 1.0, "lam": 0.25, "floor": -0.25}  # until a league's own fit is saved
FLOOR_PCT = 0.2  # the finder only pitches deals whose partner side reads at least as well as the bottom 20% of real accepted sides


# ---- one side -------------------------------------------------------------------------------------------

def best_lineup(players: Iterable[Mapping[str, Any]], slots: list[str]) -> tuple[float, dict[str, float]]:
    """Best starting lineup points a week from a roster (assets with pos and ppg), and the points per position."""
    pool = sorted((p for p in players if p.get("pos") and p.get("ppg")), key=lambda p: -float(p["ppg"]))
    used: set[int] = set()
    by_pos: dict[str, float] = defaultdict(float)
    for slot in [s for s in slots if s in FIXED] + [s for s in slots if s in FLEX]:
        ok = {slot} if slot in FIXED else FLEX.get(slot, set())
        for i, p in enumerate(pool):
            if i not in used and p["pos"] in ok:
                used.add(i)
                by_pos[str(p["pos"])] += float(p["ppg"])
                break
    return round(sum(by_pos.values()), 3), dict(by_pos)


def _total(assets: Iterable[Mapping[str, Any]], versus: int, pick_w: float) -> float:
    picks = sum(float(a.get("book") or a.get("market") or 0) for a in assets if a.get("pick")) * pick_w
    players = sorted((float(a.get("market") or 0) for a in assets if not a.get("pick")), reverse=True)
    full = max(1, versus)
    return picks + sum(players[:full]) + DEPTH * sum(players[full:])


def side_features(roster: list[Mapping[str, Any]], gives: list[Mapping[str, Any]], gets: list[Mapping[str, Any]], direction: str,
                  slots: list[str], cap_in: float = 0.0) -> dict[str, Any]:
    out_ids = {a.get("pid") for a in gives if a.get("pid")}
    in_ids = {a.get("pid") for a in gets if a.get("pid")}
    before_roster = [p for p in roster if p.get("pid") not in in_ids] + [a for a in gives if not a.get("pick") and a.get("pid") not in {p.get("pid") for p in roster}]
    after_roster = [p for p in before_roster if p.get("pid") not in out_ids] + [a for a in gets if not a.get("pick")]
    b, b_pos = best_lineup(before_roster, slots)
    a, a_pos = best_lineup(after_roster, slots)
    n_gives = sum(1 for x in gives if not x.get("pick"))
    n_gets = sum(1 for x in gets if not x.get("pick"))
    pw = PICK_W.get(direction, 0.8)
    give_v, get_v = _total(gives, n_gets, pw), _total(gets, n_gives, pw)
    future = math.log(max(0.2, min(5.0, (get_v + 1.0) / (give_v + 1.0))))
    cap = sum(float(x.get("cap") or 0) for x in gives if not x.get("pick")) - sum(float(x.get("cap") or 0) for x in gets if not x.get("pick")) + float(cap_in or 0)
    by_pos = {pos: round(a_pos.get(pos, 0.0) - b_pos.get(pos, 0.0), 2) for pos in set(a_pos) | set(b_pos)}
    return {"lineup_gain": round(a - b, 3), "by_pos": {k: v for k, v in by_pos.items() if abs(v) >= 0.05}, "future": round(future, 4),
            "cap_freed": round(cap, 2), "direction": direction, "picks_in": sum(1 for x in gets if x.get("pick")),
            "picks_out": sum(1 for x in gives if x.get("pick"))}


def utility(f: Mapping[str, Any], w: Mapping[str, float]) -> float:
    n = NOW_W.get(str(f.get("direction")), 0.5)
    return (w["L"] * n * float(f["lineup_gain"]) + w["F"] * (1 - n) * float(f["future"]) + w["C"] * float(f["cap_freed"]) / 10.0)


def trade_score(us: list[float], w: Mapping[str, float]) -> float:
    return min(us) + w["lam"] * sum(us)


# ---- the old engine's view, for the baseline -----------------------------------------------------------

OLD_OPEN = 0.85


def old_score(sides: Mapping[str, Mapping[str, Any]], feats: Mapping[str, Mapping[str, Any]]) -> float:
    """The finder as it ranked ideas: value shares by each owner's window (picks by the pick book, contracts on), the asker's
    lineup gain and surplus, a small bonus for the partner's lineup gain; ideas the partner wouldn't take at OPEN_SHARE never shown.
    A real trade has no known asker, so both owners take a turn and the better reading counts."""
    from league_ai.trade_backtest import side_share

    best = -1e9
    owners = list(sides)
    for me in owners:
        for them in owners:
            if them == me:
                continue
            w_me, w_them = NOW_W[feats[me]["direction"]], NOW_W[feats[them]["direction"]]
            their_share = side_share(sides[them], w_them, "picks+contract")
            my_share = side_share(sides[me], w_me, "picks+contract")
            if their_share is None or my_share is None or their_share < OLD_OPEN:
                continue
            surplus = 1 - 1 / my_share if my_share > 0 else -1.0
            my_gain, their_gain = feats[me]["lineup_gain"], feats[them]["lineup_gain"]
            if my_gain <= 0 and feats[me]["direction"] != "rebuilding":
                continue
            if feats[them]["direction"] == "contending" and their_gain < -0.5:
                continue
            best = max(best, my_gain + 10 * surplus + 3 * min(their_share - 1, 0.5) + 0.5 * min(their_gain, 3.0))
    return best


def market_score(sides: Mapping[str, Mapping[str, Any]]) -> float:
    """Plain dynasty value: how close to even the trade is (the short side's share)."""
    from league_ai.trade_backtest import side_share

    shares = [side_share(s, 0.0) for s in sides.values()]
    return min(x for x in shares if x is not None) if all(x is not None for x in shares) else -1.0


# ---- decoys ----------------------------------------------------------------------------------------------

def decoys(trade: Mapping[str, Any], n: int = DECOYS, kind: str = "any", seed: int | None = None) -> list[dict[str, dict[str, list]]]:
    """Other trades the same two owners could have made at that moment: the same number of players each way, picks as in the
    real one, players drawn from their rosters. kind 'value' keeps each player's market value within 0.67-1.5x of the real one."""
    rng = random.Random(seed if seed is not None else hash(str(trade.get("tid"))) & 0xFFFFFFFF)
    owners = list(trade["sides"])
    if len(owners) != 2:
        return []
    a, b = owners
    rost = trade.get("rosters") or {}
    out = []
    tries = 0
    real = {o: frozenset(x.get("pid") for x in trade["sides"][o]["gives"] if not x.get("pick")) for o in owners}
    while len(out) < n and tries < n * 20:
        tries += 1
        gives = {}
        for o in owners:
            picks = [x for x in trade["sides"][o]["gives"] if x.get("pick")]
            players = [x for x in trade["sides"][o]["gives"] if not x.get("pick")]
            pool = [p for p in rost.get(o) or [] if float(p.get("market") or 0) > 0]
            chosen: list[Mapping[str, Any]] = []
            for x in players:
                if kind == "value":
                    mv = float(x.get("market") or 0)
                    cands = [p for p in pool if p not in chosen and mv > 0 and 0.67 * mv <= float(p.get("market") or 0) <= 1.5 * mv]
                else:
                    cands = [p for p in pool if p not in chosen]
                chosen.append(rng.choice(cands) if cands else x)
            gives[o] = chosen + picks
        if all(frozenset(x.get("pid") for x in gives[o] if not x.get("pick")) == real[o] for o in owners):
            continue
        out.append({a: {"gives": gives[a], "gets": gives[b]}, b: {"gives": gives[b], "gets": gives[a]}})
    return out


def features(trade: Mapping[str, Any], sides: Mapping[str, Mapping[str, Any]], slots: list[str]) -> dict[str, dict[str, Any]]:
    return {o: side_features(list((trade.get("rosters") or {}).get(o) or []), list(s["gives"]), list(s["gets"]),
                             (trade.get("directions") or {}).get(o, "middle"), slots) for o, s in sides.items()}


# ---- the test --------------------------------------------------------------------------------------------

def _auc(real: float, fakes: list[float]) -> float | None:
    if not fakes:
        return None
    return sum(1.0 if real > f else 0.5 if real == f else 0.0 for f in fakes) / len(fakes)


def evaluate(trades: list[Mapping[str, Any]], slots: list[str] | None = None, n: int = DECOYS) -> dict[str, Any]:
    """Baseline (plain value, the old finder) and the fitted fit score, ranked against decoys. Leave-one-out: a trade
    never grades weights it helped fit."""
    slots = slots or DEFAULT_SLOTS
    usable = [t for t in trades if len(t["sides"]) == 2 and t.get("rosters") and all(t["rosters"].get(o) for o in t["sides"])]
    combos = [dict(L=l, C=c, F=f, lam=lam) for l, c, f, lam in product(GRID_L, GRID_C, GRID_F, GRID_LAM) if l or f]
    rows = []  # per trade: real features + decoy features by kind
    for t in usable:
        real_f = features(t, t["sides"], slots)
        dec = {k: [(d, features(t, d, slots)) for d in decoys(t, n, k)] for k in ("any", "value")}
        rows.append((t, real_f, dec))
    base: dict[str, dict[str, list[float]]] = {k: {"market": [], "old_finder": []} for k in ("any", "value")}
    combo_auc = [[None] * len(rows) for _ in combos]
    for i, (t, rf, dec) in enumerate(rows):
        for k in ("any", "value"):
            if not dec[k]:
                continue
            for name, fn in (("market", lambda s, f: market_score(s)), ("old_finder", old_score)):
                a = _auc(fn(t["sides"], rf), [fn(d, df) for d, df in dec[k]])
                if a is not None:
                    base[k][name].append(a)
        for j, w in enumerate(combos):
            real = trade_score([utility(f, w) for f in rf.values()], w)
            parts = [_auc(real, [trade_score([utility(f, w) for f in df.values()], w) for _, df in dec[k]]) for k in ("any", "value")]
            parts = [p for p in parts if p is not None]
            combo_auc[j][i] = sum(parts) / len(parts) if parts else None
    m = len(rows)
    totals = [sum(x for x in col if x is not None) for col in combo_auc]
    counts = [sum(1 for x in col if x is not None) for col in combo_auc]
    best_j = max(range(len(combos)), key=lambda j: totals[j] / counts[j] if counts[j] else -1) if combos and m else None
    loo_any, loo_value = [], []
    for i, (t, rf, dec) in enumerate(rows):
        j = max(range(len(combos)), key=lambda j: (totals[j] - (combo_auc[j][i] or 0)) / max(1, counts[j] - (combo_auc[j][i] is not None)))
        w = combos[j]
        real = trade_score([utility(f, w) for f in rf.values()], w)
        for k, bucket in (("any", loo_any), ("value", loo_value)):
            a = _auc(real, [trade_score([utility(f, w) for f in df.values()], w) for _, df in dec[k]])
            if a is not None:
                bucket.append(a)
    best = combos[best_j] if best_j is not None else dict(DEFAULT_WEIGHTS)
    sides_u = sorted(utility(f, best) for _, rf, _ in rows for f in rf.values())
    floor = sides_u[int(FLOOR_PCT * len(sides_u))] if sides_u else DEFAULT_WEIGHTS["floor"]
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    return {
        "trades": len(trades), "graded": m,
        "auc": {"any_decoy": {"market": avg(base["any"]["market"]), "old_finder": avg(base["any"]["old_finder"]), "fit_loo": avg(loo_any)},
                "same_value_decoy": {"market": avg(base["value"]["market"]), "old_finder": avg(base["value"]["old_finder"]), "fit_loo": avg(loo_value)}},
        "weights": {**best, "floor": round(floor, 4)},
        "real_sides": describe(rows, best),
        "old_gate": gate_rates(rows),
        "new_gate": gate_rates(rows, best, floor),
    }


def gate_rates(rows: list[tuple], w: Mapping[str, float] | None = None, floor: float | None = None) -> dict[str, Any]:
    """How often the finder would have let a deal through: real trades vs decoys. The old gate is both shares at OPEN_SHARE
    by window value (the finder's opening-offer bar); the new one is both sides' utility at or above the floor."""
    from league_ai.trade_backtest import side_share

    def ok(sides: Mapping[str, Any], feats: Mapping[str, Any]) -> bool:
        if w is None:
            shares = [side_share(s, NOW_W[feats[o]["direction"]], "picks+contract") for o, s in sides.items()]
            return all(x is not None and x >= OLD_OPEN for x in shares)
        return all(utility(f, w) >= floor for f in feats.values())

    real = [ok(t["sides"], rf) for t, rf, _ in rows]
    fake = [ok(d, df) for _, _, dec in rows for d, df in dec["any"]]
    rate = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    return {"real_pass": rate(real), "decoy_pass": rate(fake)}


def describe(rows: list[tuple], w: Mapping[str, float]) -> dict[str, Any]:
    """What real accepted trades did for each side (counts only)."""
    sides = [(f, sum(1 for x in rf.values() if x["lineup_gain"] > 0)) for _, rf, _ in rows for f in rf.values()]
    both_lineups = sum(1 for _, rf, _ in rows if all(f["lineup_gain"] > 0 for f in rf.values()))
    by_dir: dict[str, dict[str, int]] = defaultdict(lambda: {"sides": 0, "lineup_up": 0, "future_up": 0, "reads_well": 0})
    for f, _ in sides:
        d = by_dir[f["direction"]]
        d["sides"] += 1
        d["lineup_up"] += f["lineup_gain"] > 0
        d["future_up"] += f["future"] > 0
        d["reads_well"] += utility(f, w) > 0
    dumps = [f for f, _ in sides if f["cap_freed"] > 0 and f["lineup_gain"] < 0 and (f["picks_in"] or f["future"] > 0)]
    return {"trades_both_lineups_better": f"{both_lineups}/{len(rows)}",
            "trades_both_read_well": f"{sum(1 for _, rf, _ in rows if all(utility(f, w) > 0 for f in rf.values()))}/{len(rows)}",
            "by_direction": dict(by_dir),
            "sell_for_picks_or_cap": f"{len(dumps)} sides, {sum(1 for f in dumps if utility(f, w) > 0)} read as sensible"}


# ---- loading rosters and directions at each trade ---------------------------------------------------------

def load_lineup_rosters(client: Any, league_id: str) -> dict[str, list[tuple[tuple[int, int], list[str], dict[str, str]]]]:
    """owner (casefold) -> [((season, week), player ids on the roster that week, id -> position)], oldest first."""
    out: dict[str, list] = defaultdict(list)
    start = 0
    while True:
        try:
            page = (client.table("league_history_events").select("season, week, owner_name, details").eq("league_id", league_id)
                    .eq("kind", "lineup").order("event_id").range(start, start + 999).execute().data or [])
        except Exception:
            break
        for r in page:
            d = r.get("details") or {}
            people = list(d.get("starters") or []) + list(d.get("bench") or [])
            ids = [str(p["id"]) for p in people if p.get("id")]
            out[str(r.get("owner_name") or "").casefold()].append(((int(r["season"]), int(r["week"])), ids, {str(p["id"]): p.get("pos") for p in people if p.get("id")}))
        if len(page) < 1000:
            break
        start += 1000
    return {k: sorted(v, key=lambda x: x[0]) for k, v in out.items()}


def load_records(client: Any, league_id: str) -> dict[str, list[tuple[int, int, bool, float]]]:
    """owner (casefold) -> [(season, week, won, points)] regular season."""
    try:
        rows = (client.table("league_matchups").select("season, week, owner_name, won, points, is_playoff").eq("league_id", league_id)
                .limit(20000).execute().data or [])
    except Exception:
        return {}
    out: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.get("is_playoff") or r.get("points") is None:
            continue
        out[str(r.get("owner_name") or "").casefold()].append((int(r["season"]), int(r["week"]), bool(r.get("won")), float(r["points"])))
    return dict(out)


def directions_at(records: Mapping[str, list[tuple[int, int, bool, float]]], season: int, week: int) -> dict[str, str]:
    """Each owner's direction at a moment: win share then points that season before the week (3+ games), else last season's.
    Top three contending, bottom three rebuilding (10 teams)."""
    def form(games: list[tuple[int, int, bool, float]]) -> tuple[float, float] | None:
        if len(games) < 3:
            return None
        return (sum(g[2] for g in games) / len(games), sum(g[3] for g in games) / len(games))

    now = {o: form([g for g in gs if g[0] == season and g[1] < (week or 99)]) for o, gs in records.items()}
    if sum(1 for v in now.values() if v) < max(4, len(records) // 2):
        now = {o: form([g for g in gs if g[0] == season - 1]) for o, gs in records.items()}
    ranked = sorted((o for o, v in now.items() if v), key=lambda o: now[o], reverse=True)
    n = len(ranked)
    third = max(2, round(n * 0.3))
    return {o: "contending" if i < third else "rebuilding" if i >= n - third else "middle" for i, o in enumerate(ranked)}


def attach_rosters(trades: list[dict[str, Any]], rosters: Mapping[str, list], records: Mapping[str, list],
                   value: Callable[[str, Mapping[str, Any]], dict[str, Any]]) -> None:
    """Each side's roster from the last lineup before the trade, made consistent with the trade itself (what it gives is on it,
    what it gets isn't), every player valued as of the trade."""
    for t in trades:
        at = (int(t["season"]), int(t["week"]) or 0)
        dirs = directions_at(records, *at)
        t["directions"] = {o: dirs.get(o.casefold(), "middle") for o in t["sides"]}
        t["rosters"] = {}
        for o, side in t["sides"].items():
            weeks = rosters.get(o.casefold()) or []
            i = bisect_right([w for w, _, _ in weeks], (at[0], at[1] - 1 if at[1] else 0)) if weeks else 0
            if i == 0 and weeks and at[1] == 0:
                i = bisect_right([w for w, _, _ in weeks], (at[0] - 1, 99))
            if not i:
                continue
            ids = set(weeks[i - 1][1])
            ids -= {a.get("pid") for a in side["gets"] if a.get("pid")}
            ids |= {a.get("pid") for a in side["gives"] if a.get("pid")}
            t["rosters"][o] = [value(pid, t) for pid in sorted(ids)]


# ---- saving / loading the fit -----------------------------------------------------------------------------

def save(client: Any, league_id: str, result: Mapping[str, Any], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    client.table("league_ai_experiments").insert({
        "league_id": league_id, "user_id": "00000000-0000-0000-0000-000000000000", "experiment": EXPERIMENT, "question": "weekly trade fit refit",
        "result": {"weights": result["weights"], "auc": result["auc"], "graded": result["graded"], "computed_at": now.isoformat()}, "cost_usd": 0,
    }).execute()


def load_weights(client: Any, league_id: str) -> dict[str, float]:
    """The league's latest fitted weights; the defaults until there's a fit (or it graded too few trades to trust)."""
    try:
        rows = (client.table("league_ai_experiments").select("result").eq("league_id", league_id).eq("experiment", EXPERIMENT)
                .order("created_at", desc=True).limit(1).execute().data or [])
    except Exception:
        rows = []
    res = (rows[0].get("result") or {}) if rows else {}
    w = res.get("weights") or {}
    if int(res.get("graded") or 0) < MIN_TRADES or not all(k in w for k in DEFAULT_WEIGHTS):
        return dict(DEFAULT_WEIGHTS)
    return {k: float(w[k]) for k in DEFAULT_WEIGHTS}


MIN_TRADES = 15


def slots_for(client: Any, league_id: str) -> list[str]:
    try:
        from league_ai.settings_store import load_settings

        rp = load_settings(client, league_id).roster_positions or []
    except Exception:
        rp = []
    return [s for s in rp if s not in {"BN", "IR", "TAXI"}] or list(DEFAULT_SLOTS)


def main() -> None:
    """Every league: the baseline and the fit score on every completed trade. Counts and rates only."""
    import json
    import os
    import sys

    from supabase import create_client

    from league_ai.trade_backtest import load_trades

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    do_save = "--save" in sys.argv
    for n, lg in enumerate(client.table("leagues").select("id").eq("is_test", False).execute().data or [], start=1):
        trades = load_trades(client, lg["id"], with_rosters=True)
        with_r = sum(1 for t in trades if t.get("rosters") and len(t["rosters"]) == 2)
        print(f"league {n}: {len(trades)} completed trades, {with_r} with both rosters", flush=True)
        if not with_r:
            continue
        result = evaluate(trades, slots_for(client, lg["id"]))
        print(json.dumps(result, indent=1, default=str), flush=True)
        if do_save and result["graded"] >= MIN_TRADES:
            save(client, lg["id"], result)
            print("  saved", flush=True)


if __name__ == "__main__":
    main()
