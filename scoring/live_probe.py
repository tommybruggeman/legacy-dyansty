"""Live feed probe: how far behind real plays the free feeds are, and what a game-day watcher would cost.

Runs for a set number of minutes during live games. Every few seconds it reads:
- ESPN's public scoreboard and each live game's summary (plays carry a wallclock time), and
- Sleeper's public weekly stat lines (the feed the scoring engine reads).
It times when each touchdown first shows up in each feed against the play's own wallclock, and records the
bytes downloaded, CPU time and memory the loop needed. Alert matching (does this play touch a rostered
player?) is run once per league, `--leagues` times, so the cost of a second league shows up in the numbers.

Prints numbers only (the repo and its Actions logs are public): no player, team or owner names.

    python -m scoring.live_probe --minutes 30 --every 5 --leagues 2
"""
from __future__ import annotations

import argparse
import resource
import time
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any

import requests

ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
SLEEPER = "https://api.sleeper.app"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
TD_KEYS = ("rush_td", "rec_td", "fum_rec_td", "def_td", "st_td", "int_ret_td", "def_st_td")
BIG_PLAY_YARDS = 40


class Meter:
    def __init__(self) -> None:
        self.bytes = 0
        self.calls = 0
        self.session = requests.Session()

    def get(self, url: str) -> Any:
        try:
            resp = self.session.get(url, timeout=10)
            self.bytes += len(resp.content)
            self.calls += 1
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            print(f"[probe] fetch failed: {type(exc).__name__}", flush=True)
            return None


def _ts(text: str | None) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def espn_plays(summary: Any) -> list[dict[str, Any]]:
    """Every play in a game summary: {id, team, wallclock, scoring, yards}."""
    drives = (summary or {}).get("drives") or {}
    groups = list(drives.get("previous") or [])
    if drives.get("current"):
        groups.append(drives["current"])
    out = []
    for drive in groups:
        team = str(((drive.get("team") or {}).get("abbreviation")) or "")
        team = {"WSH": "WAS"}.get(team, team)  # ESPN's code -> Sleeper's
        for play in drive.get("plays") or []:
            out.append({"id": str(play.get("id") or ""), "team": team, "wallclock": _ts(play.get("wallclock")),
                        "scoring": bool(play.get("scoringPlay")), "yards": play.get("statYardage") or 0,
                        "td": "touchdown" in str((play.get("type") or {}).get("text") or "").lower() or "touchdown" in str(play.get("text") or "").lower()})
    return out


def sleeper_team_tds(rows: Any) -> tuple[dict[str, float], set[str]]:
    """NFL team -> touchdowns credited to its players so far, plus the set of player ids with a stat line."""
    tds: dict[str, float] = defaultdict(float)
    ids: set[str] = set()
    for row in rows or []:
        stats = row.get("stats") or {}
        team = str(row.get("team") or (row.get("player") or {}).get("team") or "")
        ids.add(str(row.get("player_id") or ""))
        tds[team] += sum(float(stats.get(k) or 0) for k in TD_KEYS)
    return dict(tds), ids


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0.0


def run(minutes: float, every: float, leagues: int, season: int | None, week: int | None) -> list[str]:
    meter = Meter()
    state = meter.get(f"{SLEEPER}/v1/state/nfl") or {}
    season = season or int(state.get("season") or 0)
    week = week or int(state.get("week") or 0)
    query = "season_type=regular&" + "&".join(f"position[]={p}" for p in POSITIONS)
    rostered = [set() for _ in range(leagues)]  # filled from the stat feed below: a stand-in league per copy

    seen_play: dict[str, float] = {}
    espn_lag: list[float] = []
    all_lag: dict[str, list[float]] = {"plain": [], "fresh": []}  # every new play; odd loops add a cache-busting param
    board_lag: list[float] = []  # the scoreboard's own last-play line (no extra download)
    seen_last: set[str] = set()
    espn_td_times: dict[str, list[float]] = defaultdict(list)  # team -> wallclock of each TD play, in order
    sleeper_prev: dict[str, float] | None = None
    sleeper_seen: dict[str, list[float]] = defaultdict(list)  # team -> when each new TD first showed in Sleeper
    alerts = 0
    loops = 0
    cpu0 = time.process_time()
    end = time.time() + minutes * 60
    while time.time() < end:
        started = time.time()
        loops += 1
        board = meter.get(f"{ESPN}/scoreboard") or {}
        live = [e for e in board.get("events") or [] if ((e.get("status") or {}).get("type") or {}).get("state") == "in"]
        mode = "fresh" if loops % 2 else "plain"
        for event in live:
            last = ((event.get("competitions") or [{}])[0].get("situation") or {}).get("lastPlay") or {}
            if last.get("id") and str(last["id"]) not in seen_last:
                seen_last.add(str(last["id"]))
                wall = seen_play.get(str(last["id"]))  # first time the summary showed the same play
                if loops > 1 and wall is None:
                    board_lag.append(0.0)  # scoreboard had it before the summary did
                elif loops > 1 and wall is not None:
                    board_lag.append(started - wall)  # seconds the scoreboard trailed the summary
            bust = f"&_={int(started)}" if mode == "fresh" else ""
            for play in espn_plays(meter.get(f"{ESPN}/summary?event={event.get('id')}{bust}")):
                if not play["id"] or play["id"] in seen_play:
                    continue
                seen_play[play["id"]] = started
                if loops == 1 or play["wallclock"] is None:
                    continue  # plays already on the board when the probe started have no fair first-seen time
                lag = started - play["wallclock"]
                all_lag[mode].append(lag)
                if play["scoring"] or play["td"] or abs(play["yards"] or 0) >= BIG_PLAY_YARDS:
                    espn_lag.append(lag)
                if play["td"]:
                    espn_td_times[play["team"]].append(play["wallclock"])
                for league in rostered:  # alert matching, once per league
                    alerts += 1 if league and (play["td"] or abs(play["yards"] or 0) >= BIG_PLAY_YARDS) else 0
        rows = meter.get(f"{SLEEPER}/stats/nfl/{season}/{week}?{query}")
        tds, ids = sleeper_team_tds(rows)
        if not any(rostered):
            pool = sorted(i for i in ids if i)
            for n in range(leagues):
                rostered[n] = set(pool[n::max(leagues, 1) + 2][:210])  # ~210 rostered players per league, like ours
        if sleeper_prev is not None:
            for team, n in tds.items():
                for _ in range(int(round(n - sleeper_prev.get(team, 0.0)))):
                    sleeper_seen[team].append(started)
        sleeper_prev = tds
        time.sleep(max(0.0, every - (time.time() - started)))

    # Sleeper lag: each new Sleeper TD for a team, matched in order to that team's ESPN TD plays seen during the probe.
    sleeper_lag = []
    for team, seen in sleeper_seen.items():
        plays = sorted(espn_td_times.get(team) or [])
        for wall, first in zip(plays, seen):
            if first >= wall:
                sleeper_lag.append(first - wall)
    cpu = time.process_time() - cpu0
    elapsed_h = max(minutes / 60, 1e-9)
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    out = [f"probe: {minutes:g} min, every {every:g}s, {loops} loops, {leagues} league(s), season {season} week {week}"]
    for name, lags in (("ESPN big plays/scores", espn_lag), ("Sleeper touchdowns", sleeper_lag)):
        out.append(f"  {name}: {len(lags)} timed; median {median(lags) if lags else 0:.0f}s, 90th pct {pct(lags, 0.9):.0f}s, worst {max(lags, default=0):.0f}s")
    for mode, lags in all_lag.items():
        out.append(f"  ESPN every play ({mode} url): {len(lags)} timed; median {median(lags) if lags else 0:.0f}s, 90th pct {pct(lags, 0.9):.0f}s")
    out.append(f"  ESPN scoreboard last play vs summary: {len(board_lag)} timed; median {median(board_lag) if board_lag else 0:.0f}s behind")
    out.append(f"  alert checks run: {alerts}")
    out.append(f"  download: {meter.bytes / 1e6 / elapsed_h:.0f} MB/hour over {meter.calls} calls; CPU {cpu / elapsed_h:.0f} s/hour; peak memory {peak_mb:.0f} MB")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--every", type=float, default=5)
    ap.add_argument("--leagues", type=int, default=2)
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    args = ap.parse_args()
    for line in run(args.minutes, args.every, args.leagues, args.season, args.week):
        print(line, flush=True)


if __name__ == "__main__":
    main()
