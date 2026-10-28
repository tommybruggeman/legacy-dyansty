"""How each owner runs a team, from the league's own activity log (league_history_events): weekly lineups, FAAB bids,
adds and drops, and rookie drafts. No model call, nothing stored: a few hundred rows a season, read when asked.

The log is the app's, not Sleeper's: Sleeper is one feed into it today, and moves made in the app land in the same
rows, so the habits keep updating whichever app the league plays in. Every season is kept, so each profile gets
sharper year over year, and recent seasons count double.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Iterable, Mapping

RECENT_WEIGHT = 2.0   # this season and last count double
MIN_CALLS = 6         # close start/sit calls needed before naming a lean


def _weight(season: int, current: int | None) -> float:
    return RECENT_WEIGHT if current is None or season >= current - 1 else 1.0


def lineup_habits(lineups: Iterable[Mapping[str, Any]], current: int | None = None) -> dict[str, dict[str, Any]]:
    """Per owner: share of the best possible lineup scored, and on close calls (two players at a position where recent
    form and season-long scoring disagree) how often they started the hot hand."""
    lineups = sorted(lineups, key=lambda r: (int(r.get("season") or 0), int(r.get("week") or 0)))
    history: dict[tuple[int, str], list[float]] = defaultdict(list)   # (season, player) -> points by week, every team
    eff: dict[str, list[tuple[float, float]]] = defaultdict(list)
    calls: dict[str, list[tuple[float, float]]] = defaultdict(list)   # (went with form?, weight)
    by_week: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for r in lineups:
        by_week[(int(r["season"]), int(r["week"]))].append(r)
    for (season, _), rows in sorted(by_week.items()):
        w = _weight(season, current)
        for r in rows:
            d = r.get("details") or {}
            owner = str(r.get("owner_name") or "")
            if d.get("best"):
                eff[owner].append((min(1.0, float(d.get("points") or 0) / float(d["best"])), w))
            starters, bench = d.get("starters") or [], d.get("bench") or []
            for s in starters:
                for b in bench:
                    if s.get("pos") != b.get("pos"):
                        continue
                    hs, hb = history[(season, str(s["id"]))], history[(season, str(b["id"]))]
                    if len(hs) < 3 or len(hb) < 3:
                        continue
                    form_s, form_b = sum(hs[-2:]) / 2, sum(hb[-2:]) / 2
                    long_s, long_b = sum(hs) / len(hs), sum(hb) / len(hb)
                    if (form_s > form_b) != (long_s > long_b):   # form and season-long disagree: a real call
                        calls[owner].append((1.0 if form_s > form_b else 0.0, w))
        for r in rows:   # add this week's points after the decisions were made
            d = r.get("details") or {}
            for p in [*(d.get("starters") or []), *(d.get("bench") or [])]:
                history[(season, str(p["id"]))].append(float(p.get("pts") or 0))
    out = {}
    for owner in set(eff) | set(calls):
        e, c = eff.get(owner, []), calls.get(owner, [])
        row: dict[str, Any] = {}
        if e:
            row["lineup_vs_best"] = round(sum(x * w for x, w in e) / sum(w for _, w in e), 3)
        if len(c) >= MIN_CALLS:
            share = sum(x * w for x, w in c) / sum(w for _, w in c)
            row["hot_hand_share"] = round(share, 2)
            row["start_sit_lean"] = "starts the hot hand" if share >= 0.6 else "trusts season-long production" if share <= 0.4 else "no strong lean"
        row["close_calls"] = len(c)
        out[owner] = row
    return out


def market_habits(events: Iterable[Mapping[str, Any]], current: int | None = None) -> dict[str, dict[str, Any]]:
    """Per owner: FAAB spend and timing, positions bid on, add/drop volume, how long an add stays before he's cut,
    and what they take early in rookie drafts."""
    spend: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    early: dict[str, float] = defaultdict(float)
    bids: dict[str, list[float]] = defaultdict(list)
    bid_pos: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    moves: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    added: dict[tuple[str, str], tuple[int, int]] = {}
    held: dict[str, list[int]] = defaultdict(list)
    drafted: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in sorted(events, key=lambda r: (int(r.get("season") or 0), int(r.get("week") or 0))):
        kind, owner = r.get("kind"), str(r.get("owner_name") or "")
        season, week, pos = int(r.get("season") or 0), int(r.get("week") or 0), str(r.get("position") or "?")
        if kind in ("waiver", "add"):
            moves[owner][season] += 1
            added[(owner, str(r.get("player_id")))] = (season, week)
            bid = float(r.get("faab_bid") or 0)
            if kind == "waiver" and bid > 0:
                spend[owner][season] += bid
                bids[owner].append(bid)
                bid_pos[owner][pos] += bid
                if week <= 4:
                    early[owner] += bid
        elif kind == "drop":
            moves[owner][season] += 1
            start = added.pop((owner, str(r.get("player_id"))), None)
            if start and start[0] == season:
                held[owner].append(week - start[1])
        elif kind == "draft_pick" and (r.get("details") or {}).get("round") in (1, "1") and (r.get("details") or {}).get("draft_type") == "linear":
            # linear drafts are rookie drafts here; startup drafts were auctions or snakes
            drafted[owner][pos] += 1
    out: dict[str, dict[str, Any]] = {}
    for owner in set(spend) | set(moves) | set(drafted):
        row: dict[str, Any] = {}
        seasons = sorted(moves[owner]) or sorted(spend[owner])
        if seasons:
            row["moves_per_season"] = round(sum(moves[owner].values()) / len(seasons), 1)
        total = sum(spend[owner].values())
        if total:
            row["faab_per_season"] = round(total / max(len(spend[owner]), 1), 1)
            row["biggest_bid"] = max(bids[owner])
            row["share_spent_by_week_4"] = round(early[owner] / total, 2)
            row["faab_by_position"] = {k: round(v / total, 2) for k, v in sorted(bid_pos[owner].items(), key=lambda kv: -kv[1])[:3]}
        if len(held[owner]) >= 3:
            weeks = median(held[owner])
            row["weeks_before_cutting_an_add"] = weeks
            row["patience"] = "quick to cut" if weeks <= 2 else "patient" if weeks >= 6 else "average"
        if drafted[owner]:
            row["first_round_rookies_by_position"] = dict(drafted[owner])
        out[owner] = row
    return out


def owner_tendencies(client: Any, league_id: str, current_season: int | None = None) -> dict[str, dict[str, Any]]:
    """Both profiles, merged by owner, plus a one-line style for prompts."""
    rows, start = [], 0
    while True:
        try:
            page = (client.table("league_history_events").select("kind, season, week, owner_name, player_id, position, faab_bid, details")
                    .eq("league_id", league_id).in_("kind", ["lineup", "waiver", "add", "drop", "draft_pick"]).order("event_id")
                    .range(start, start + 999).execute().data or [])
        except Exception:
            break
        rows += page
        if len(page) < 1000:
            break
        start += 1000
    lineups = lineup_habits([r for r in rows if r.get("kind") == "lineup"], current_season)
    market = market_habits([r for r in rows if r.get("kind") != "lineup"], current_season)
    out = {}
    for owner in set(lineups) | set(market):
        row = {**lineups.get(owner, {}), **market.get(owner, {})}
        row["style"] = describe(row)
        out[owner] = row
    return out


def describe(row: Mapping[str, Any]) -> str:
    bits = []
    if row.get("start_sit_lean") and row["start_sit_lean"] != "no strong lean":
        bits.append(f"{row['start_sit_lean']} ({round(row['hot_hand_share'] * 100)}% of {row['close_calls']} close calls)")
    if row.get("lineup_vs_best"):
        bits.append(f"lineups score {round(row['lineup_vs_best'] * 100)}% of their best")
    if row.get("faab_per_season"):
        top = next(iter(row.get("faab_by_position") or {}), None)
        bits.append(f"spends ${row['faab_per_season']:g} FAAB a season, {round(row['share_spent_by_week_4'] * 100)}% by week 4"
                    + (f", mostly on {top}s" if top else ""))
    if row.get("patience"):
        bits.append(f"{row['patience']} with adds (cuts after {row['weeks_before_cutting_an_add']:g} weeks)")
    if row.get("first_round_rookies_by_position"):
        bits.append("first-round rookies: " + ", ".join(f"{k} {v}" for k, v in row["first_round_rookies_by_position"].items()))
    return "; ".join(bits) or "not enough activity yet"
