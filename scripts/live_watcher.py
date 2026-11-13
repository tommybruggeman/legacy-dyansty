"""Game-day live stats watcher: one small loop that keeps every league's live scores current, for free.

Every INTERVAL seconds it reads ESPN's free NFL scoreboard (one small call covers every game). It downloads a game's
summary when the game changed (new play, score, clock or state) and, for live games, at least every SUMMARY_EVERY
seconds anyway (the scoreboard can trail the summary by a few seconds, which made our scores ~15 s slower than ESPN's
own feed in the 2026-11-09 speed test) and rebuild its players' stat
lines (scoring/espn_feed.py). It saves only the games that changed (one row per game in live_stat_lines), which
the server scores with our engine for every league and every owner (api/live_stats.py). Phones never call ESPN.

Runs as the main watcher on a free Google e2-micro (deploy/live-watcher/), with a backup on GitHub Actions
(.github/workflows/live-watcher.yml) that stands by while the main one is saving. Between games it naps.

Prints counts only (the repo and its Actions logs are public): no player, team or owner names.

Environment: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY; ROLE (main | backup | cloudrun, default main); INTERVAL (seconds,
default 2); SUMMARY_EVERY (seconds, default 2; 0 = only on scoreboard changes); FEED (summary | gamecast); MAX_MINUTES (0 = run forever, the
e2-micro default); SEASON_OFFSET (default 0; a test lane saves under season + offset, which the app never reads).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring import espn_feed  # noqa: E402

TABLE = "live_stat_lines"
SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"  # our player ids are Sleeper ids for now; only the id list
ID_REFRESH = 24 * 3600
NAP = 60          # no game on: check the scoreboard once a minute
LONG_NAP = 600    # nothing kicks off within the hour
MAIN_FRESH = 60   # the backup stands by while the main watcher saved within this many seconds
HEARTBEAT = 20    # with nothing new, the main watcher still stamps the row this often (so the backup stands by)
POSITIONS = {"QB", "RB", "WR", "TE", "K"}


def slim(lines: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fantasy positions only, zero and display-only stats dropped: keeps the saved row (and every read of it) small."""
    out = {}
    for pid, line in lines.items():
        if line.get("pos") not in POSITIONS:
            continue
        stats = {k: v for k, v in (line.get("stats") or {}).items() if v and not k.endswith("_lng") and not k.startswith("idp_")}
        if stats:
            out[pid] = {"stats": stats, "pos": line["pos"]}
    return out


class Feed:
    """HTTP with byte and call counts (printed, so the free-tier numbers stay visible)."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "legacy-dynasty-live-watcher"
        self.bytes = 0
        self.calls = 0

    def get(self, url: str, timeout: int = 15) -> Any:
        resp = self.session.get(url, timeout=timeout)
        self.bytes += len(resp.content)
        self.calls += 1
        resp.raise_for_status()
        return resp.json()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _kickoffs(board: dict[str, Any]) -> list[datetime]:
    out = []
    for event in board.get("events") or []:
        try:
            out.append(datetime.fromisoformat(str(event.get("date")).replace("Z", "+00:00")))
        except ValueError:
            pass
    return out


class Watcher:
    def __init__(self, client: Any, feed: Feed, role: str):
        self.client = client
        self.feed = feed
        self.role = role
        self.idmap: espn_feed.IdMap | None = None
        self.id_loaded = 0.0
        self.versions: dict[str, str] = {}               # game id -> scoreboard version last read
        self.game_lines: dict[str, dict[str, Any]] = {}  # game id -> our player id -> line
        self.week: tuple[int, int] | None = None
        self.saved: dict[str, str] = {}  # game id -> digest of what was last saved
        self.beat = 0.0
        self.fetched: dict[str, float] = {}  # game id -> when its summary was last downloaded
        self.counts = {"saves": 0, "summaries": 0, "standby": 0, "errors": 0, "unmatched": 0}

    def ids(self) -> espn_feed.IdMap:
        if self.idmap is None or time.time() - self.id_loaded > ID_REFRESH:
            try:
                players = self.feed.get(SLEEPER_PLAYERS, timeout=60)
            except Exception:
                players = {}
            try:
                from pipeline.nfl_data.common import fetch_text
                from pipeline.nfl_data.crosswalk import CROSSWALK_URL, Crosswalk
                crosswalk = Crosswalk.from_csv(fetch_text(CROSSWALK_URL))
            except Exception:
                crosswalk = None
            if players or self.idmap is None:
                self.idmap = espn_feed.IdMap(players, crosswalk)
                self.id_loaded = time.time()
        return self.idmap

    def main_is_saving(self, season: int, week: int) -> bool:
        rows = (self.client.table(TABLE).select("source,updated_at").eq("season", season + SEASON_OFFSET).eq("week", week)
                .order("updated_at", desc=True).limit(1).execute().data or [])
        if not rows or rows[0].get("source") != "main":
            return False
        try:
            stamp = datetime.fromisoformat(str(rows[0]["updated_at"]).replace("Z", "+00:00"))
        except ValueError:
            return False
        return (_now() - stamp).total_seconds() < MAIN_FRESH

    def tick(self) -> float:
        """One pass. Returns how long to wait before the next one."""
        board = self.feed.get(f"{espn_feed.ESPN}/scoreboard")
        season = int(((board.get("season") or {}).get("year")) or 0)
        week = int(((board.get("week") or {}).get("number")) or 0)
        season_type = int(((board.get("season") or {}).get("type")) or 2)
        if not season or not week or season_type != 2:
            return LONG_NAP
        if self.week != (season, week):  # new week: start clean
            self.week, self.versions, self.game_lines, self.saved = (season, week), {}, {}, {}
        games = espn_feed.games(board)
        live = [g for g in games if g["state"] == "in"]
        if not live and all(g["id"] in self.versions and self.versions[g["id"]] == g["version"] for g in games if g["state"] == "post"):
            soon = [k for k in _kickoffs(board) if timedelta(0) <= k - _now() <= timedelta(hours=1)]
            return NAP if soon else LONG_NAP
        if self.role == "backup" and self.main_is_saving(season, week):
            self.counts["standby"] += 1
            return 30
        idmap = self.ids()
        for g in games:
            changed = self.versions.get(g["id"]) != g["version"]
            due = g["state"] == "in" and SUMMARY_EVERY > 0 and time.time() - self.fetched.get(g["id"], 0) >= SUMMARY_EVERY - 0.5
            if g["state"] == "pre" or not (changed or due):
                continue
            self.fetched[g["id"]] = time.time()
            try:
                summary = espn_feed.game_data(FEED, self.feed.get(espn_feed.game_url(FEED, g["id"])))
            except Exception:
                self.counts["errors"] += 1
                continue
            self.counts["summaries"] += 1
            lines, unmatched = espn_feed.week_lines([summary], idmap)
            self.counts["unmatched"] = unmatched
            self.game_lines[g["id"]] = slim(lines)
            self.versions[g["id"]] = g["version"]
        from api.nfl_live import parse_scoreboard
        state = parse_scoreboard(board)
        for g in games:  # save only the games whose lines or state changed since the last save
            if g["id"] not in self.game_lines:
                continue
            game_state = {t: state[t] for t in g["teams"] if t in state}
            digest = hashlib.sha1(json.dumps([self.game_lines[g["id"]], game_state], sort_keys=True).encode()).hexdigest()
            if digest == self.saved.get(g["id"]):
                continue
            self.client.table(TABLE).upsert({
                "season": season + SEASON_OFFSET, "week": week, "game_id": g["id"], "lines": self.game_lines[g["id"]], "games": game_state,
                "source": self.role, "updated_at": _now().isoformat(),
            }, on_conflict="season,week,game_id").execute()
            self.counts["saves"] += 1
            self.saved[g["id"]] = digest
            self.beat = time.time()
        if self.role == "main" and time.time() - self.beat > HEARTBEAT:
            self.client.table(TABLE).upsert({"season": season + SEASON_OFFSET, "week": week, "game_id": "_watcher", "source": "main",
                                             "updated_at": _now().isoformat()}, on_conflict="season,week,game_id").execute()
            self.beat = time.time()
        return INTERVAL if live else NAP


INTERVAL = float(os.getenv("INTERVAL", "2"))
SUMMARY_EVERY = float(os.getenv("SUMMARY_EVERY", "2"))
FEED = os.getenv("FEED", "summary")  # summary | gamecast (ESPN's live game tracker; same box score + plays)
SEASON_OFFSET = int(os.getenv("SEASON_OFFSET", "0"))


def main() -> int:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    role = os.getenv("ROLE", "main")
    feed = Feed()
    watcher = Watcher(client, feed, role)
    minutes = float(os.getenv("MAX_MINUTES", "0"))
    stop = time.time() + minutes * 60 if minutes > 0 else float("inf")
    passes, started_at = 0, time.time()
    print(f"live watcher ({role}) started", flush=True)
    while time.time() < stop:
        began = time.time()
        try:
            wait = watcher.tick()
        except Exception as exc:
            watcher.counts["errors"] += 1
            print(f"WATCH_ERROR {type(exc).__name__}", flush=True)
            wait = 30
        passes += 1
        if role == "cloudrun" and wait >= LONG_NAP:
            break  # started by a timer: nothing on within the hour, so stop now instead of napping on the clock
        if passes % 120 == 0:
            hours = max((time.time() - started_at) / 3600, 1e-9)
            print(f"pass {passes}: {watcher.counts}; {feed.calls} calls, {feed.bytes / hours / 1e6:.0f} MB/hour", flush=True)
        time.sleep(max(0.0, min(wait - (time.time() - began), stop - time.time())))
    print(f"done after {passes} passes: {watcher.counts}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
