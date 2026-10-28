"""Sync every league's full Sleeper history (all seasons via previous_league_id) into
league_history_events and league_matchups.

    python -m pipeline.league_history.run            # all leagues with a Sleeper id
    python -m pipeline.league_history.run --league <uuid>
"""
from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any

from pipeline.league_history.build import OwnerMap, build_draft_events, build_lineup_events, build_matchups, build_transaction_events
from pipeline.nfl_data.common import fetch_json, log_run, now_iso, service_client, upsert_rows

SLEEPER = "https://api.sleeper.app/v1"
MAX_WEEKS = 18


def _get(url: str) -> Any:
    try:
        return fetch_json(url, timeout=60)
    except Exception as exc:
        print(f"[league_history] {url}: {type(exc).__name__}", flush=True)
        return None


def league_chain(sleeper_league_id: str) -> list[dict[str, Any]]:
    """Current league first, then each previous season's league."""
    chain = []
    seen = set()
    lid = sleeper_league_id
    while lid and lid not in seen and len(chain) < 15:
        seen.add(lid)
        league = _get(f"{SLEEPER}/league/{lid}")
        if not league:
            break
        chain.append(league)
        lid = league.get("previous_league_id")
    return chain


def load_players() -> dict[str, dict[str, Any]]:
    return _get(f"{SLEEPER}/players/nfl") or {}


def contract_map(client: Any, league_ids: list[str], season: int | None = None) -> dict[str, dict[str, Any]]:
    """sleeper_player_id -> {salary, years} (best effort).

    With a season: the salary each player's contract paid that season, read from every app league given. Last
    season's contracts live under last season's league row, so pass both to get what owners actually paid then.
    Without one: the first-season salary of the latest agreement in the first league (the old behaviour).
    """
    out: dict[str, dict[str, Any]] = {}
    for league_id in [l for l in league_ids if l]:
        try:
            agreements = client.table("contract_agreements").select("id, sleeper_player_id, status, created_at").eq("league_id", league_id).order("created_at").execute().data or []
            ids = [a["id"] for a in agreements]
            seasons = []
            for i in range(0, len(ids), 200):
                seasons += client.table("contract_seasons").select("contract_id, season, salary, obligation_status").in_("contract_id", ids[i:i + 200]).execute().data or []
        except Exception as exc:
            print(f"[league_history] contract map unavailable ({type(exc).__name__})", flush=True)
            continue
        by_contract: dict[str, list[dict[str, Any]]] = {}
        for s in seasons:
            by_contract.setdefault(str(s["contract_id"]), []).append(s)
        for a in agreements:
            rows = by_contract.get(str(a["id"]), [])
            pid = str(a.get("sleeper_player_id"))
            if season is None:
                if rows:
                    first = min(rows, key=lambda r: r.get("season") or 0)
                    out[pid] = {"salary": first.get("salary"), "years": len(rows)}
                continue
            that = [r for r in rows if r.get("season") == season and r.get("obligation_status") not in ("voided",)]
            if that and pid not in out:  # first league listed wins
                out[pid] = {"salary": that[0].get("salary"), "years": len(rows)}
        if season is None:
            break
    return out


def sync_league(client: Any, league: dict[str, Any], players: dict[str, dict[str, Any]]) -> int:
    league_id = str(league["id"])
    sleeper_id = str(league.get("sleeper_league_id") or "")
    if not sleeper_id:
        return 0
    teams = client.table("league_teams").select("id, owner_name, team_name, sleeper_roster_id, sleeper_user_id").eq("league_id", league_id).execute().data or []
    settings_rows = client.table("league_ai_settings").select("*").eq("league_id", league_id).limit(1).execute().data or []
    ai = settings_rows[0] if settings_rows else {}
    # an app league row per Sleeper season (last season's league keeps its own row and its own contracts)
    app_league = {str(l.get("sleeper_league_id")): str(l["id"]) for l in client.table("leagues").select("id, sleeper_league_id").execute().data or [] if l.get("sleeper_league_id")}
    fallback = contract_map(client, [league_id])
    written = 0
    for sl in league_chain(sleeper_id):
        season = int(sl.get("season") or 0)
        sid = sl["league_id"]
        contracts = contract_map(client, [app_league.get(str(sid), ""), league_id], season)
        if str(sid) == sleeper_id:  # today's contracts only label this season's moves, never an older season's
            contracts = {**fallback, **contracts}
        users = _get(f"{SLEEPER}/league/{sid}/users") or []
        rosters = _get(f"{SLEEPER}/league/{sid}/rosters") or []
        owners = OwnerMap(users, rosters, teams)
        playoff_start = (sl.get("settings") or {}).get("playoff_week_start")
        events: list[dict[str, Any]] = []
        matchups: list[dict[str, Any]] = []
        for week in range(1, MAX_WEEKS + 1):
            txs = _get(f"{SLEEPER}/league/{sid}/transactions/{week}") or []
            events.extend(build_transaction_events(league_id, season, txs, owners, players, contracts))
            ms = _get(f"{SLEEPER}/league/{sid}/matchups/{week}") or []
            events.extend(build_lineup_events(league_id, season, week, ms, owners, players, sl.get("roster_positions") or []))
            matchups.extend(build_matchups(league_id, season, week, ms, owners, top_n=int(ai.get("top_scorer_bonus_count", 5) or 5),
                                           win_points=int(ai.get("win_points", 2) or 2), bonus_points=int(ai.get("top_scorer_bonus_points", 1) or 1), playoff_start_week=playoff_start))
        for draft in _get(f"{SLEEPER}/league/{sid}/drafts") or []:
            picks = _get(f"{SLEEPER}/draft/{draft['draft_id']}/picks") or []
            events.extend(build_draft_events(league_id, season, draft, picks, owners, players))
        written += upsert_rows(client, "league_history_events", events, on_conflict="league_id,event_id")
        written += upsert_rows(client, "league_matchups", matchups, on_conflict="league_id,season,week,owner_name")
        print(f"[league_history] {league.get('name')} {season}: {len(events)} events, {len(matchups)} matchup rows", flush=True)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default=None, help="league uuid (default: every league with a Sleeper id)")
    args = parser.parse_args(argv)
    client = service_client()
    q = client.table("leagues").select("id, name, sleeper_league_id")
    if args.league:
        q = q.eq("id", args.league)
    leagues = [l for l in (q.execute().data or []) if l.get("sleeper_league_id")]
    players = load_players()
    ok = True
    for league in leagues:
        started = now_iso()
        try:
            n = sync_league(client, league, players)
            log_run(client, "league_history", started_at=started, rows_written=n, ok=True, detail=str(league.get("name")))
        except Exception as exc:
            ok = False
            log_run(client, "league_history", started_at=started, rows_written=0, ok=False, detail=f"{league.get('name')}: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1200:]}")
            print(f"[league_history] {league.get('name')}: FAILED {type(exc).__name__}: {exc}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
