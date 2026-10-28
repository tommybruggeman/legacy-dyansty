"""Backtest the trade engine on every completed trade in the league. No model call.

Trades are rare, so waiting for new accepts and declines teaches the engine slowly. Every completed trade
already shows two owners saying yes. Replaying them asks: valued the way the engine values players
(this-season production vs dynasty market, weighted by each owner's window), would both sides have come
out at or above what they gave? That gives

  * a hit rate, which any change to the trade logic has to match or beat before it ships, and
  * each owner's revealed lean: the now-vs-future weight that best explains the trades they made
    (an owner who keeps buying veterans for picks trades like a contender whatever the standings say).

Values are taken as of each trade: market value from the nearest scrape on or before the trade date,
production from that season's games before the trade week.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from league_ai.trade_finder import OPEN_SHARE

WEIGHTS = (0.0, 0.2, 0.35, 0.5, 0.65, 0.8)  # 0.0 = market only, the old engine
DEFAULT_WEIGHT = 0.5
PICK_NOW_SHARE = {0.0: 1.0}  # picks don't score this season: they count (1 - 0.6 * w) of market


def asset_value(a: Mapping[str, Any], w: float, variant: str = "current") -> float:
    """variant: "current" = the engine as it was; "picks" = picks priced by PickBook (market blended with what slots
    really became); "injury" = dynasty value tilted for a recent injury; "both"."""
    if a.get("pick"):
        market = float((a.get("book") if variant in ("picks", "both") and a.get("book") else a.get("market")) or 0)
        return market * (1 - 0.6 * w)
    market = float(a.get("market") or 0) * (float(a.get("inj") or 1.0) if variant in ("injury", "both") else 1.0)
    return w * float(a.get("now") or 0) + (1 - w) * market


def side_share(side: Mapping[str, list[Mapping[str, Any]]], w: float, variant: str = "current") -> float | None:
    gives = sum(asset_value(a, w, variant) for a in side.get("gives") or [])
    gets = sum(asset_value(a, w, variant) for a in side.get("gets") or [])
    if gives <= 0:
        return None if gets <= 0 else 9.99
    return gets / gives


def fit_weight(trades: Iterable[Mapping[str, Any]], owner: str) -> float | None:
    """The now weight that best explains an owner's trades: highest average log share (they all said yes)."""
    sides = [t["sides"][owner] for t in trades if owner in t["sides"]]
    if len(sides) < 2:
        return None
    def fit(w: float) -> float:
        shares = [side_share(s, w) for s in sides]
        return sum(math.log(max(0.2, min(s, 3.0))) for s in shares if s is not None)
    best = max(WEIGHTS[1:], key=fit)
    return best if fit(best) > fit(DEFAULT_WEIGHT) + 0.05 else DEFAULT_WEIGHT


def backtest(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Hit rate (both sides at or above OPEN_SHARE) for market-only, one default weight, and each owner's
    learned weight (leave-one-out, so a trade never grades the weight it helped fit)."""
    owners = sorted({o for t in trades for o in t["sides"]})
    learned = {o: fit_weight(trades, o) for o in owners}

    def hit(t: Mapping[str, Any], weight_of) -> bool | None:
        shares = [side_share(s, weight_of(o)) for o, s in t["sides"].items()]
        if any(s is None for s in shares) or not shares:
            return None
        return all(s >= OPEN_SHARE for s in shares)

    results: dict[str, list[bool]] = defaultdict(list)
    for i, t in enumerate(trades):
        others = trades[:i] + trades[i + 1:]
        loo = {o: fit_weight(others, o) or DEFAULT_WEIGHT for o in t["sides"]}
        for name, fn in (("market_only", lambda o: 0.0), ("default", lambda o: DEFAULT_WEIGHT), ("learned", lambda o: loo[o])):
            h = hit(t, fn)
            if h is not None:
                results[name].append(h)
    rate = {k: round(sum(v) / len(v), 3) if v else None for k, v in results.items()}
    return {
        "trades": len(trades),
        "graded": len(results.get("default", [])),
        "hit_rate": rate,
        "owner_now_weight": {o: w for o, w in learned.items() if w is not None},
        "owner_trades": {o: sum(1 for t in trades if o in t["sides"]) for o in owners},
    }


VARIANTS = ("current", "picks", "injury", "both")


def compare_variants(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Each valuation change against the current engine on every completed trade: hit rate (both sides at or above
    OPEN_SHARE, default now weight) and how far the two sides' shares sit apart (a fair trade has them near equal;
    lower is better)."""
    out: dict[str, Any] = {}
    for v in VARIANTS:
        hits, gaps, lows = [], [], []
        for t in trades:
            shares = [side_share(sd, DEFAULT_WEIGHT, v) for sd in t["sides"].values()]
            if not shares or any(x is None for x in shares):
                continue
            hits.append(all(x >= OPEN_SHARE for x in shares))
            logs = [math.log(max(0.2, min(x, 5.0))) for x in shares]
            gaps.append(max(logs) - min(logs))
            lows.append(min(shares))
        out[v] = {"graded": len(hits), "hit_rate": round(sum(hits) / len(hits), 3) if hits else None,
                  "imbalance": round(sum(gaps) / len(gaps), 3) if gaps else None,
                  "median_low_share": round(sorted(lows)[len(lows) // 2], 3) if lows else None}
    assets = [a for t in trades for sd in t["sides"].values() for a in sd["gets"]]
    out["assets_without_market"] = sum(1 for a in assets if not a.get("market"))
    out["assets"] = len(assets)
    out["trades_with_picks"] = sum(1 for t in trades if any(a.get("pick") for sd in t["sides"].values() for a in sd["gets"]))
    out["trades_with_injured"] = sum(1 for t in trades if any((a.get("inj") or 1.0) < 1.0 for sd in t["sides"].values() for a in sd["gets"]))
    return out


def lean_label(w: float | None) -> str | None:
    if w is None:
        return None
    return "contending" if w >= 0.65 else "rebuilding" if w <= 0.35 else "middle"


# ---- loading trades and values as of each trade -----------------------------------------------------

def load_trades(client: Any, league_id: str) -> list[dict[str, Any]]:
    rows = (client.table("league_history_events").select("event_id, season, week, occurred_at, owner_name, player_id, player_name, position, details")
            .eq("league_id", league_id).eq("kind", "trade").limit(5000).execute().data or [])
    by_tid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        parts = str(r.get("event_id") or "").split(":")
        if len(parts) >= 2 and parts[0] == "sleeper":
            by_tid[parts[1]].append(r)
    pids = sorted({str(r["player_id"]) for r in rows if r.get("player_id")})
    market = _market_history(client, pids)
    positions = _positions(client, pids)
    seasons = sorted({int(r["season"]) for r in rows if r.get("season")})
    production = {s: _season_weeks(client, s, positions) for s in seasons}
    curves = _now_curves(client)
    picks = _pick_values(client)
    episodes = _injury_episodes(client, pids)
    from league_ai.pick_values import PickBook
    books: dict[int, PickBook] = {}
    pick_rows = _pick_rows_raw(client)
    trades = []
    for tid, legs in by_tid.items():
        first = legs[0]
        season, week = int(first.get("season") or 0), int(first.get("week") or 0)
        when = str(first.get("occurred_at") or "")[:10]
        details = first.get("details") or {}
        sides: dict[str, dict[str, list[dict[str, Any]]]] = {o: {"gets": [], "gives": []} for o in details.get("participants") or []}
        movers = {(p.get("player"), p.get("to")): p.get("from") for p in details.get("players") or []}
        for r in legs:
            to = r.get("owner_name")
            if r.get("position") == "PICK":
                label = str(r.get("player_name") or "")
                frm = next((p.get("from") for p in details.get("picks") or [] if label.startswith(str(p.get("pick"))) and p.get("to") == to), None)
                if season not in books:
                    books[season] = PickBook(pick_rows, teams=10, rounds=3, season=season)
                book = books[season]
                asset = {"pick": 1.0, "market": _pick_value(picks, label, season), "book": book.value(label)[0], "name": label}
            else:
                pid = str(r.get("player_id") or "")
                frm = movers.get((r.get("player_name"), to))
                pos = positions.get(pid) or r.get("position")
                ppg = _ppg(production.get(season, {}), pid, week) or _ppg(production.get(season - 1, {}), pid, 99)
                asset = {"market": _value_at(market.get(pid, []), when), "now": _now_value(curves, pos, ppg, production.get(season, {}), positions, week),
                         "name": r.get("player_name"), "inj": _injury_mult(episodes.get(pid, []), season, week)}
            if to in sides:
                sides[to]["gets"].append(asset)
            if frm in sides:
                sides[frm]["gives"].append(asset)
        if len(sides) >= 2 and all(s["gets"] and s["gives"] for s in sides.values()):
            trades.append({"tid": tid, "season": season, "week": week, "date": when, "sides": sides})
    return sorted(trades, key=lambda t: t["date"])


def _injury_episodes(client: Any, pids: list[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for i in range(0, len(pids), 150):
        try:
            for r in (client.table("nfl_injury_episodes").select("sleeper_id, last_game_season, last_game_week, injury, games_missed")
                      .in_("sleeper_id", pids[i:i + 150]).execute().data or []):
                out[str(r["sleeper_id"])].append(r)
        except Exception:
            pass
    return out


def _injury_mult(episodes: list[dict[str, Any]], season: int, week: int) -> float:
    """The injury tilt as it would have stood at the trade: only absences that had started before it."""
    from league_ai.injuries import recent_injury

    before = [e for e in episodes if (int(e.get("last_game_season") or 0), int(e.get("last_game_week") or 0)) < (season, week or 99)]
    found = recent_injury(before, season)
    return found[0] if found else 1.0


def _pick_rows_raw(client: Any) -> list[dict[str, Any]]:
    from league_ai.trade_finder import _pick_rows

    return _pick_rows(client)


def _market_history(client: Any, pids: list[str]) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for i in range(0, len(pids), 40):
        try:
            for r in (client.table("nfl_market_values").select("sleeper_id, scrape_date, value_2qb").in_("sleeper_id", pids[i:i + 40])
                      .limit(20000).execute().data or []):
                if r.get("value_2qb") is not None:
                    out[str(r["sleeper_id"])].append((str(r["scrape_date"])[:10], float(r["value_2qb"])))
        except Exception:
            pass
    return {k: sorted(v) for k, v in out.items()}


def _value_at(history: list[tuple[str, float]], day: str) -> float:
    """Market value on the latest scrape on or before the day; the earliest scrape if the trade predates them all."""
    if not history:
        return 0.0
    i = bisect_right([d for d, _ in history], day)
    return history[i - 1][1] if i else history[0][1]


def _positions(client: Any, pids: list[str]) -> dict[str, str]:
    out = {}
    for i in range(0, len(pids), 150):
        try:
            for r in client.table("nfl_players").select("sleeper_id, position").in_("sleeper_id", pids[i:i + 150]).execute().data or []:
                if r.get("position"):
                    out[str(r["sleeper_id"])] = str(r["position"])
        except Exception:
            pass
    return out


def _season_weeks(client: Any, season: int, positions: dict[str, str] | None = None) -> dict[str, list[tuple[int, float]]]:
    """sleeper_id -> [(week, points)] for one regular season, paged. Fills `positions` for every player with games, so a
    traded player's production rank is taken among all players at his position, not only the other traded ones."""
    out: dict[str, list[tuple[int, float]]] = defaultdict(list)
    start = 0
    while True:
        try:
            rows = (client.table("nfl_player_stats").select("sleeper_id, week, position, fantasy_points_ppr").eq("season", season).eq("season_type", "REG")
                    .range(start, start + 999).execute().data or [])
        except Exception:
            break
        for r in rows:
            out[str(r["sleeper_id"])].append((int(r.get("week") or 0), float(r.get("fantasy_points_ppr") or 0)))
            if positions is not None and r.get("position"):
                positions.setdefault(str(r["sleeper_id"]), str(r["position"]))
        if len(rows) < 1000:
            break
        start += 1000
    return out


def _ppg(weeks: Mapping[str, list[tuple[int, float]]], pid: str, before_week: int) -> float | None:
    pts = [p for w, p in weeks.get(pid, []) if w < before_week]
    return sum(pts) / len(pts) if len(pts) >= 3 else None


def _now_curves(client: Any) -> dict[str, list[float]]:
    """position -> market values best first, from the latest scrape: what the market pays for the k-th best at a position."""
    try:
        latest = client.table("nfl_market_values").select("scrape_date").order("scrape_date", desc=True).limit(1).execute().data or []
        rows = (client.table("nfl_market_values").select("position, value_2qb").eq("scrape_date", latest[0]["scrape_date"]).limit(5000).execute().data or []) if latest else []
    except Exception:
        rows = []
    curves: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("value_2qb") is not None:
            curves[str(r.get("position"))].append(float(r["value_2qb"]))
    return {k: sorted(v, reverse=True) for k, v in curves.items()}


def _now_value(curves: Mapping[str, list[float]], pos: str | None, ppg: float | None, weeks: Mapping[str, list[tuple[int, float]]],
               positions: Mapping[str, str], week: int) -> float:
    """Market value of the player ranked where this one ranks by production at his position that season."""
    curve = curves.get(str(pos)) or []
    if ppg is None or not curve:
        return 0.0
    peers = [x for pid, w in weeks.items() if positions.get(pid) == pos for x in [_ppg(weeks, pid, week or 99)] if x is not None]
    rank = sum(1 for x in peers if x > ppg)  # players producing more, among those traded or rostered with stats
    return curve[min(rank, len(curve) - 1)]


def _pick_values(client: Any) -> list[tuple[int, int, float]]:
    try:
        rows = client.table("nfl_pick_values").select("draft_year, round, value_2qb, scrape_date").order("scrape_date", desc=True).limit(400).execute().data or []
    except Exception:
        return []
    latest = rows[0]["scrape_date"] if rows else None
    return [(int(r["draft_year"]), int(r["round"]), float(r["value_2qb"])) for r in rows
            if r.get("scrape_date") == latest and r.get("round") and r.get("value_2qb") and r.get("draft_year")]


def _pick_value(picks: list[tuple[int, int, float]], label: str, season: int) -> float:
    """'2027 R1 pick (orig. X)' -> average current value of that round; years out are priced like next year's picks."""
    try:
        year, rnd = int(label.split()[0]), int(label.split()[1].lstrip("Rr"))
    except Exception:
        return 0.0
    vals = [v for y, r, v in picks if r == rnd and y == year] or [v for y, r, v in picks if r == rnd]
    return sum(vals) / len(vals) if vals else 0.0


def save_styles(client: Any, league_id: str, result: Mapping[str, Any], now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rows = [{"league_id": league_id, "owner_name": o, "now_weight": w, "lean": lean_label(w), "trades": result["owner_trades"].get(o, 0),
             "computed_at": now.isoformat()} for o, w in result["owner_now_weight"].items()]
    if rows:
        client.table("league_ai_owner_trade_styles").upsert(rows, on_conflict="league_id,owner_name").execute()
    return len(rows)


def load_styles(client: Any, league_id: str) -> dict[str, str]:
    """owner name -> revealed lean (contending / middle / rebuilding) from the last backtest."""
    try:
        rows = client.table("league_ai_owner_trade_styles").select("owner_name, lean").eq("league_id", league_id).limit(100).execute().data or []
    except Exception:
        return {}
    return {str(r["owner_name"]): str(r["lean"]) for r in rows if r.get("lean")}


def main() -> None:
    """Compare valuation changes on every league's completed trades. Prints counts and rates only (the repo is public)."""
    import os

    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    for n, lg in enumerate(client.table("leagues").select("id").execute().data or [], start=1):
        trades = load_trades(client, lg["id"])
        print(f"league {n}: {len(trades)} completed trades")
        if trades:
            print(f"  hit rate by now weight: {backtest(trades)['hit_rate']}")
            print(f"  valuation changes: {compare_variants(trades)}")


if __name__ == "__main__":
    main()
