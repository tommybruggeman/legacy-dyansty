"""The league's activity feed: every trade, pickup, drop, commissioner move and draft pick in one place, newest first.

Shared by the app server (/league/activity, League > Activity and Home's "Latest in the league") and the website's
Activity page. Past seasons come from league_history_events (synced nightly); this season is read straight from
Sleeper's transactions (cached a couple of minutes), so a move shows up within minutes of happening.
Trades carry the full trade card (api/trade_feed.py).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Iterable, Mapping

from api import sleeper, trade_feed

KINDS = ("trade", "add", "waiver", "drop", "commissioner", "draft_pick")
MOVE_KINDS = {"add", "waiver", "drop", "commissioner"}
MAX_WEEKS = 18


def _f(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def history_rows(client: Any, league_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    start = 0
    while True:
        page = (client.table("league_history_events")
                .select("event_id, kind, season, week, occurred_at, owner_name, player_id, player_name, position, faab_bid, contract_salary, contract_years, details")
                .eq("league_id", league_id).in_("kind", list(KINDS)).range(start, start + 999).execute().data or [])
        out += page
        if len(page) < 1000:
            return out
        start += 1000


def waiver_bids(txs: Iterable[Mapping[str, Any]], owners: Any) -> dict[tuple[int, str], list[dict[str, Any]]]:
    """(week, player id) -> every blind bid on that player in that waiver run, winner first then highest.
    Sleeper keeps the losing claims as 'failed' waiver transactions."""
    out: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for t in txs:
        if t.get("type") != "waiver" or t.get("status") not in {"complete", "failed"}:
            continue
        week = int(t.get("leg") or t.get("week") or 0)
        amount = _f((t.get("settings") or {}).get("waiver_bid")) or 0.0
        for pid, rid in (t.get("adds") or {}).items():
            out[(week, str(pid))].append({"owner": owners.name(rid), "amount": amount, "won": t.get("status") == "complete"})
    for bids in out.values():
        bids.sort(key=lambda b: (not b["won"], -b["amount"]))
    return dict(out)


def live_rows(league_id: str, sleeper_id: str, season: int, week: int, league_teams: list[Mapping[str, Any]],
              players: Callable[[Iterable[str]], Mapping[str, Mapping[str, Any]]], contracts: Mapping[str, Mapping[str, Any]],
              get: Callable[..., Any] = sleeper.get) -> tuple[list[dict[str, Any]], dict[tuple[int, str], list[dict[str, Any]]]]:
    """This season's transactions straight from Sleeper, shaped like league_history_events rows, plus the waiver bids."""
    from pipeline.league_history.build import OwnerMap, build_transaction_events

    txs: list[dict[str, Any]] = []
    for w in range(1, max(1, min(week, MAX_WEEKS)) + 1):
        txs += get(f"league/{sleeper_id}/transactions/{w}", ttl=120 if w >= week - 1 else 3600) or []
    owners = OwnerMap(get(f"league/{sleeper_id}/users", ttl=600) or [], get(f"league/{sleeper_id}/rosters", ttl=600) or [], list(league_teams))
    ids = {str(p) for t in txs for p in list((t.get("adds") or {})) + list((t.get("drops") or {}))}
    info = {pid: {"full_name": r.get("full_name"), "position": r.get("position")} for pid, r in players(ids).items()}
    return build_transaction_events(league_id, season, txs, owners, info, contracts), waiver_bids(txs, owners)


def merge(history: list[Mapping[str, Any]], live: list[Mapping[str, Any]], season: int) -> list[Mapping[str, Any]]:
    """History for past seasons (and draft picks), Sleeper's live list for this season's transactions."""
    if not live:
        return list(history)
    keep = [r for r in history if not (r.get("season") == season and r.get("kind") != "draft_pick")]
    return keep + list(live)


def _tid(row: Mapping[str, Any]) -> str:
    parts = str(row.get("event_id") or "").split(":")
    return parts[1] if len(parts) >= 2 else str(row.get("event_id"))


def _player(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"player_id": str(row.get("player_id") or ""), "name": row.get("player_name") or "Player", "pos": row.get("position") or "",
            "salary": _f(row.get("contract_salary")), "years": row.get("contract_years")}


def _drop_charges(state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    from services.team_roster_state import state_cap_adjustments

    rows = state_cap_adjustments({"dead_cap": state.get("dead_cap", []), "cap_adjustments": state.get("cap_adjustments", [])})
    return [r for r in rows if r.get("adjustment_type") == "dropped_player_charge"]


def _charge_keys(r: Mapping[str, Any]) -> set[str]:
    return {str(r.get("sleeper_player_id") or r.get("player_id") or ""), str(r.get("player_name") or "").casefold()} - {""}


def dead_cap_index(states: list[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """(team id, player id or lower-case name) -> {amount, through} for this season's dropped-player charges;
    `states` is this season's team state and the seasons after it (api/teams.seasons_state)."""
    if not states:
        return {}
    season = int(states[0].get("season") or 0)
    later = [{(str(r.get("league_team_id")), k) for r in _drop_charges(s) for k in _charge_keys(r)} for s in states[1:]]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in _drop_charges(states[0]):
        team, keys = str(r.get("league_team_id")), _charge_keys(r)
        years = 1
        for charged in later:  # consecutive seasons still charged
            if not any((team, k) in charged for k in keys):
                break
            years += 1
        for k in keys:
            out[(team, k)] = {"amount": _f(r.get("amount")) or 0.0, "through": season + years - 1}
    return out


def moves(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Adds, waiver claims, drops and commissioner moves, one item per transaction (an add and its drop together)."""
    by_tid: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("kind") in MOVE_KINDS:
            by_tid[_tid(r)].append(r)
    out = []
    for tid, legs in by_tid.items():
        first = legs[0]
        adds = [r for r in legs if r.get("kind") != "drop"]
        drops = [r for r in legs if r.get("kind") == "drop"]
        kind = next((r["kind"] for r in adds), None) or ("commissioner" if (first.get("details") or {}).get("type") == "commissioner" else "drop")
        out.append({
            "kind": "move", "type": {"waiver": "waiver", "add": "free_agent", "commissioner": "commissioner"}.get(kind, "drop"),
            "id": tid, "season": int(first.get("season") or 0), "week": int(first.get("week") or 0),
            "date": str(first.get("occurred_at") or "")[:10], "at": str(first.get("occurred_at") or ""),
            "owner": first.get("owner_name"), "owners": sorted({str(r.get("owner_name")) for r in legs if r.get("owner_name")}),
            "adds": [{**_player(r), "faab": _f(r.get("faab_bid"))} for r in adds], "drops": [_player(r) for r in drops],
        })
    return out


def drafts(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One item per draft, picks in order."""
    by_draft: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("kind") == "draft_pick":
            parts = str(r.get("event_id") or "").split(":")
            by_draft[parts[2] if len(parts) >= 3 else "draft"].append(r)
    out = []
    for did, picks in by_draft.items():
        picks = sorted(picks, key=lambda r: int((r.get("details") or {}).get("overall") or 0))
        first = picks[0]
        out.append({
            "kind": "draft", "id": did, "season": int(first.get("season") or 0), "week": 0,
            "date": str(first.get("occurred_at") or "")[:10], "at": str(first.get("occurred_at") or ""),
            "owners": sorted({str(r.get("owner_name")) for r in picks if r.get("owner_name")}),
            "picks": [{**_player(r), "owner": r.get("owner_name"), "round": (r.get("details") or {}).get("round"),
                       "slot": (r.get("details") or {}).get("slot"), "amount": _f((r.get("details") or {}).get("auction_amount"))} for r in picks],
        })
    return out


def feed(client: Any, league_id: str, sleeper_id: str, current: tuple[int, int], league_teams: list[Mapping[str, Any]],
         contracts: Mapping[str, Mapping[str, Any]], *, week: int, dead_cap: Mapping[tuple[str, str], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Everything, newest first: {items, seasons}. current = (season, last finished week); week = this NFL week."""
    from api import teams

    season = current[0]
    team_ids = {str(t["owner_name"]): str(t["team_id"]) for t in league_teams if t.get("owner_name")}
    rows = history_rows(client, league_id)
    try:
        live, bids = live_rows(league_id, sleeper_id, season, week,
                         [{"owner_name": t.get("owner_name"), "sleeper_roster_id": t.get("roster_id"), "sleeper_user_id": t.get("sleeper_user_id")} for t in league_teams],
                         lambda ids: teams.player_info(client, ids), contracts)
    except Exception as exc:
        print(f"ACTIVITY_LIVE_ERROR {type(exc).__name__}: {exc}", flush=True)
        live, bids = [], {}
    rows = merge(rows, live, season)
    trades = trade_feed.group([r for r in rows if r.get("kind") == "trade"])
    trade_feed.enrich(client, trades, sleeper_id, current, team_ids)
    items = trades + moves(rows) + drafts(rows)
    info = teams.player_info(client, {p["player_id"] for i in items if i["kind"] != "trade" for p in i.get("adds", []) + i.get("drops", []) + i.get("picks", [])})
    for i in items:
        if i["kind"] == "trade":
            continue
        i["team_id"] = team_ids.get(str(i.get("owner")))
        for p in i.get("adds", []) + i.get("drops", []) + i.get("picks", []):
            p["nfl_team"] = (info.get(p["player_id"]) or {}).get("team") or "FA"
            if "owner" in p:
                p["team_id"] = team_ids.get(str(p["owner"]))
        if i["kind"] == "move":
            for p in i["adds"]:
                if i["type"] == "waiver" and (i["week"], p["player_id"]) in bids and i["season"] == season:
                    p["bids"] = bids[(i["week"], p["player_id"])]
            for p in i["drops"]:
                hit = (dead_cap or {}).get((str(i.get("team_id")), p["player_id"])) or (dead_cap or {}).get((str(i.get("team_id")), str(p["name"]).casefold()))
                if hit and i["season"] == season:
                    p["dead_cap"] = hit["amount"]
                    p["dead_through"] = hit["through"]
    items.sort(key=lambda i: (i.get("at") or "", i["id"]), reverse=True)
    return {"items": items, "seasons": sorted({i["season"] for i in items if i["season"]}, reverse=True)}
