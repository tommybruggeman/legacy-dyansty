"""CLI entry point for the nightly NFL data sync.

    python -m pipeline.nfl_data.run                 # everything, current + 3 prior seasons of stats
    python -m pipeline.nfl_data.run --only players  # one loader
    python -m pipeline.nfl_data.run --seasons 2026  # stats for given seasons only
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from typing import Any

from pipeline.nfl_data.common import fetch_json, fetch_text, log_run, now_iso, service_client, upsert_rows
from pipeline.nfl_data.crosswalk import CROSSWALK_URL, Crosswalk
from pipeline.nfl_data.injuries import ESPN_INJURIES_URLS, NFLVERSE_INJURIES_URL, read_csv as read_injuries_csv, transform_espn, transform_nflverse
from pipeline.nfl_data.market_values import PICKS_URL, VALUES_URL, read_csv as read_values_csv, transform_picks, transform_values
from pipeline.nfl_data.nflverse_stats import SNAPS_URL, STATS_URL, read_csv as read_stats_csv, transform_stats
from pipeline.nfl_data.sleeper_players import SLEEPER_PLAYERS_URL, transform_players
from pipeline.nfl_data.prospects import build_prospects, fetch_season_inputs


def current_nfl_season(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 8 else today.year - 1


def default_seasons(today: date | None = None) -> list[int]:
    cur = current_nfl_season(today)
    return [cur - 3, cur - 2, cur - 1, cur]


def _run(client: Any, name: str, fn) -> bool:
    started = now_iso()
    print(f"[nfl_data] {name}: start", flush=True)
    try:
        written = fn()
        log_run(client, name, started_at=started, rows_written=written, ok=True)
        print(f"[nfl_data] {name}: wrote {written} rows", flush=True)
        return True
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}"
        log_run(client, name, started_at=started, rows_written=0, ok=False, detail=detail)
        print(f"[nfl_data] {name}: FAILED {type(exc).__name__}: {exc}", flush=True)
        return False


def load_crosswalk() -> Crosswalk:
    return Crosswalk.from_csv(fetch_text(CROSSWALK_URL))


def sync_players(client: Any, crosswalk: Crosswalk) -> int:
    players = fetch_json(SLEEPER_PLAYERS_URL, timeout=180)
    rows = transform_players(players, crosswalk)
    return upsert_rows(client, "nfl_players", rows, on_conflict="sleeper_id")


def sync_stats(client: Any, crosswalk: Crosswalk, seasons: list[int]) -> int:
    total = 0
    for season in seasons:
        try:
            stats = read_stats_csv(fetch_text(STATS_URL.format(season=season), timeout=300))
        except Exception as exc:
            print(f"[nfl_data] stats {season}: unavailable ({type(exc).__name__})", flush=True)
            continue
        try:
            snaps = read_stats_csv(fetch_text(SNAPS_URL.format(season=season), timeout=300))
        except Exception as exc:
            print(f"[nfl_data] snaps {season}: unavailable ({type(exc).__name__}); loading stats without snap share", flush=True)
            snaps = None
        rows = transform_stats(stats, snaps, crosswalk)
        total += upsert_rows(client, "nfl_player_stats", rows, on_conflict="gsis_id,season,week,season_type")
        print(f"[nfl_data] stats {season}: {len(rows)} rows", flush=True)
    return total


def sync_values(client: Any, crosswalk: Crosswalk) -> int:
    values = read_values_csv(fetch_text(VALUES_URL))
    written = upsert_rows(client, "nfl_market_values", transform_values(values, crosswalk), on_conflict="fantasypros_id,scrape_date")
    try:
        picks = read_values_csv(fetch_text(PICKS_URL))
        written += upsert_rows(client, "nfl_pick_values", transform_picks(picks), on_conflict="pick_label,scrape_date")
    except Exception as exc:
        print(f"[nfl_data] pick values unavailable ({type(exc).__name__})", flush=True)
    return written


def sync_injuries(client: Any, crosswalk: Crosswalk, seasons: list[int]) -> int:
    written = 0
    try:
        payload = None
        last_exc: Exception | None = None
        for url in ESPN_INJURIES_URLS:
            try:
                payload = fetch_json(url, timeout=60)
                break
            except Exception as exc:  # try the next host
                last_exc = exc
        if payload is None:
            raise last_exc or RuntimeError("no ESPN host reachable")
        rows = transform_espn(payload, crosswalk)
        client.table("nfl_injuries").delete().neq("espn_id", "").execute()  # current report replaces the old one
        written += upsert_rows(client, "nfl_injuries", rows, on_conflict="espn_id")
        print(f"[nfl_data] espn injuries: {len(rows)} rows", flush=True)
    except Exception as exc:
        print(f"[nfl_data] espn injuries unavailable ({type(exc).__name__}: {exc})", flush=True)
    for season in seasons:
        try:
            frame = read_injuries_csv(fetch_text(NFLVERSE_INJURIES_URL.format(season=season), timeout=120))
        except Exception as exc:
            print(f"[nfl_data] practice reports {season}: unavailable ({type(exc).__name__})", flush=True)
            continue
        rows = transform_nflverse(frame, crosswalk)
        written += upsert_rows(client, "nfl_practice_reports", rows, on_conflict="gsis_id,season,week")
        print(f"[nfl_data] practice reports {season}: {len(rows)} rows", flush=True)
    return written


def sync_prospects(client: Any, crosswalk: Crosswalk, seasons: list[int]) -> int:
    import os

    if not os.environ.get("CFBD_API_KEY", "").strip():
        print("[nfl_data] prospects skipped: CFBD_API_KEY not set", flush=True)
        return 0
    written = 0
    for season in seasons[-3:]:  # current college season and two prior: enough for the next two rookie classes
        inputs = fetch_season_inputs(season, recruit_years=list(range(season - 4, season + 1)))
        rows = build_prospects(season, crosswalk=crosswalk, **inputs)
        written += upsert_rows(client, "nfl_prospects", rows, on_conflict="cfbd_athlete_id,season")
        print(f"[nfl_data] prospects {season}: {len(rows)} rows", flush=True)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["players", "stats", "values", "injuries", "prospects"], default=None)
    parser.add_argument("--seasons", nargs="*", type=int, default=None)
    args = parser.parse_args(argv)

    client = service_client()
    crosswalk = load_crosswalk()
    print(f"[nfl_data] crosswalk: {len(crosswalk.frame)} players", flush=True)
    seasons = args.seasons or default_seasons()

    ok = True
    if args.only in (None, "players"):
        ok &= _run(client, "sleeper_players", lambda: sync_players(client, crosswalk))
    if args.only in (None, "stats"):
        ok &= _run(client, "nflverse_stats", lambda: sync_stats(client, crosswalk, seasons))
    if args.only in (None, "values"):
        ok &= _run(client, "dynastyprocess_values", lambda: sync_values(client, crosswalk))
    if args.only in (None, "injuries"):
        ok &= _run(client, "injuries", lambda: sync_injuries(client, crosswalk, seasons))
    if args.only in (None, "prospects"):
        ok &= _run(client, "cfbd_prospects", lambda: sync_prospects(client, crosswalk, seasons))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
