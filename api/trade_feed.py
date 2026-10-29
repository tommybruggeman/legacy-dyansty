"""Every trade in the league, newest first: what each side got, cap change, and who's winning it.

Shared by the app server (/league/trades) and the website's Trades page, so both show the same numbers.
Trades come from league_history_events (kind 'trade'), which the history sync fills for every season.
Two read-outs per trade, both free (no model call):

- points since: each side's received players' points in this league's scoring from the trade week on
  (Sleeper's weekly matchups, cached), so "winning so far" means real production;
- market value now: each side's share of today's dynasty market value of what it received. Picks whose
  draft has already happened aren't priced (the pick became a player).
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Callable, Iterable, Mapping

from api import cache, sleeper

MAX_WEEKS = 18
_PICK = re.compile(r"^(20\d\d) R(\d)")


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(text: Any) -> float:
    m = re.search(r"[\d.]+", str(text or ""))
    return float(m.group()) if m else 0.0


def group(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """league_history_events trade legs -> one trade per Sleeper transaction, newest first:
    {id, season, week, date, owners, sides: [{owner, players, picks, faab, cap_change}]}."""
    by_tid: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        parts = str(r.get("event_id") or "").split(":")
        if len(parts) >= 2 and parts[0] == "sleeper":
            by_tid[parts[1]].append(r)
    trades = []
    for tid, legs in by_tid.items():
        first = legs[0]
        details = first.get("details") or {}
        owners = list(details.get("participants") or sorted({str(r.get("owner_name")) for r in legs if r.get("owner_name")}))
        player_legs = [r for r in legs if r.get("player_id") and r.get("position") != "PICK"]
        moved_from = {(p.get("player"), p.get("to")): p.get("from") for p in details.get("players") or []}
        sides = []
        for owner in owners:
            got = [r for r in player_legs if r.get("owner_name") == owner]
            gave = [r for r in player_legs if moved_from.get((r.get("player_name"), r.get("owner_name"))) == owner]
            sides.append({
                "owner": owner,
                "players": [{"player_id": str(r["player_id"]), "name": r.get("player_name") or "Player", "pos": r.get("position") or "",
                             "salary": _f(r.get("contract_salary")) or None, "years": r.get("contract_years")} for r in got],
                "picks": [{"label": str(p.get("pick") or ""), "original_owner": p.get("original_owner")}
                          for p in details.get("picks") or [] if p.get("to") == owner],
                "faab": sum(_money(b.get("amount")) for b in details.get("faab") or [] if b.get("to") == owner)
                        - sum(_money(b.get("amount")) for b in details.get("faab") or [] if b.get("from") == owner),
                "cap_change": round(sum(_f(r.get("contract_salary")) for r in got) - sum(_f(r.get("contract_salary")) for r in gave), 2),
            })
        trades.append({"id": tid, "season": int(first.get("season") or 0), "week": int(first.get("week") or 0),
                       "date": str(first.get("occurred_at") or "")[:10], "owners": owners, "sides": sides})
    return sorted(trades, key=lambda t: (t["date"], t["id"]), reverse=True)


def season_leagues(sleeper_id: str, get: Callable[..., Any] = sleeper.get) -> dict[int, str]:
    """season -> Sleeper league id, walking previous_league_id back from the current league."""
    out: dict[int, str] = {}
    lid = sleeper_id
    while lid and len(out) < 15:
        league = get(f"league/{lid}", ttl=86400) or {}
        if not league.get("season") or int(league["season"]) in out:
            break
        out[int(league["season"])] = str(lid)
        lid = league.get("previous_league_id")
    return out


def week_points(league_id: str, week: int, final: bool, get: Callable[..., Any] = sleeper.get) -> dict[str, float]:
    """player id -> points that week in the league's scoring (every rostered player)."""
    out: dict[str, float] = {}
    for row in get(f"league/{league_id}/matchups/{week}", ttl=86400 if final else 300) or []:
        for pid, pts in (row.get("players_points") or {}).items():
            out[str(pid)] = max(out.get(str(pid), 0.0), _f(pts))
    return out


def add_points_since(trades: list[dict[str, Any]], leagues: Mapping[int, str], current: tuple[int, int],
                     points_for: Callable[[str, int, bool], Mapping[str, float]]) -> None:
    """Each side's points from the trade week through the last finished week (current = (season, last finished week))."""
    now_season, now_week = current
    needed = {(s, w) for t in trades for s in range(t["season"], now_season + 1) if s in leagues
              for w in range(t["week"] if s == t["season"] else 1, (now_week if s == now_season else MAX_WEEKS) + 1)}
    jobs = {key: cache.pool.submit(points_for, leagues[key[0]], key[1], key != (now_season, now_week)) for key in sorted(needed)}
    weeks: dict[tuple[int, int], Mapping[str, float]] = {}
    for key, job in jobs.items():
        try:
            weeks[key] = job.result()
        except Exception:
            weeks[key] = {}
    for t in trades:
        span = [k for k in weeks if k >= (t["season"], t["week"])]
        for side in t["sides"]:
            for p in side["players"]:
                p["points_since"] = round(sum(weeks[k].get(p["player_id"], 0.0) for k in span), 1)
            side["points_since"] = round(sum(p["points_since"] for p in side["players"]), 1)
        t["weeks_since"] = len({k for k in span})


def market_now(client: Any, player_ids: list[str]) -> dict[str, float]:
    """sleeper id -> latest dynasty market value (2QB scale)."""
    out: dict[str, tuple[str, float]] = {}
    for i in range(0, len(player_ids), 100):
        try:
            rows = (client.table("nfl_market_values").select("sleeper_id, scrape_date, value_2qb").in_("sleeper_id", player_ids[i:i + 100])
                    .order("scrape_date", desc=True).limit(2000).execute().data or [])
        except Exception:
            rows = []
        for r in rows:
            pid, day = str(r.get("sleeper_id")), str(r.get("scrape_date") or "")
            if r.get("value_2qb") is not None and (pid not in out or day > out[pid][0]):
                out[pid] = (day, float(r["value_2qb"]))
    return {k: v for k, (_, v) in out.items()}


def pick_values(client: Any) -> dict[tuple[int, int], float]:
    """(draft year, round) -> average market value from the latest pick scrape."""
    try:
        rows = client.table("nfl_pick_values").select("draft_year, round, value_2qb, scrape_date").order("scrape_date", desc=True).limit(400).execute().data or []
    except Exception:
        return {}
    latest = rows[0].get("scrape_date") if rows else None
    acc: dict[tuple[int, int], list[float]] = defaultdict(list)
    for r in rows:
        if r.get("scrape_date") == latest and r.get("draft_year") and r.get("round") and r.get("value_2qb"):
            acc[(int(r["draft_year"]), int(r["round"]))].append(float(r["value_2qb"]))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def add_market_shares(trades: list[dict[str, Any]], values: Mapping[str, float], picks: Mapping[tuple[int, int], float], next_draft: int) -> None:
    """Each side's share of today's market value of everything that moved (picks already drafted count 0)."""
    def pick_value(label: str) -> float:
        m = _PICK.match(label)
        if not m or int(m.group(1)) < next_draft:
            return 0.0
        year, rnd = int(m.group(1)), int(m.group(2))
        same = [v for (y, r), v in picks.items() if r == rnd and y == year] or [v for (y, r), v in picks.items() if r == rnd]
        return sum(same) / len(same) if same else 0.0

    for t in trades:
        for side in t["sides"]:
            side["market_now"] = round(sum(values.get(p["player_id"], 0.0) for p in side["players"]) + sum(pick_value(p["label"]) for p in side["picks"]))
        total = sum(s["market_now"] for s in t["sides"])
        for side in t["sides"]:
            side["market_share"] = round(side["market_now"] / total, 3) if total else None


def add_verdict(trades: list[dict[str, Any]]) -> None:
    """'winning' = the side with the most points since, once at least one week has been played."""
    for t in trades:
        ranked = sorted(t["sides"], key=lambda s: -s.get("points_since", 0.0))
        lead = ranked[0]["points_since"] - ranked[1]["points_since"] if len(ranked) > 1 else 0.0
        t["winning"] = {"owner": ranked[0]["owner"], "by": round(lead, 1)} if t.get("weeks_since") and lead > 0 else None


def trades(client: Any, league_id: str, sleeper_id: str, current: tuple[int, int], team_ids: Mapping[str, str]) -> dict[str, Any]:
    """The whole feed, as the app and the website show it."""
    rows = (client.table("league_history_events")
            .select("event_id, season, week, occurred_at, owner_name, player_id, player_name, position, contract_salary, contract_years, details")
            .eq("league_id", league_id).eq("kind", "trade").limit(5000).execute().data or [])
    feed = group(rows)
    from api import teams
    pids = sorted({p["player_id"] for t in feed for s in t["sides"] for p in s["players"]})
    info = teams.player_info(client, pids)
    for t in feed:
        for side in t["sides"]:
            side["team_id"] = team_ids.get(side["owner"])
            for p in side["players"]:
                p["nfl_team"] = (info.get(p["player_id"]) or {}).get("team") or "FA"
                p["pos"] = p["pos"] or (info.get(p["player_id"]) or {}).get("position") or ""
    try:
        add_points_since(feed, season_leagues(sleeper_id), current, week_points)
    except Exception as exc:
        print(f"TRADE_FEED_POINTS_ERROR {type(exc).__name__}: {exc}", flush=True)
    # In season, this season's picks have been drafted already; next year's are the nearest still to come.
    add_market_shares(feed, market_now(client, pids), pick_values(client), next_draft=current[0] + 1)
    add_verdict(feed)
    return {"trades": feed, "seasons": sorted({t["season"] for t in feed}, reverse=True),
            "owners": sorted({o for t in feed for o in t["owners"]})}
