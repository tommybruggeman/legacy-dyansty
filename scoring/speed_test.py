"""Live scoring speed test: how far behind the real play our own scores are, next to ESPN's feed and Sleeper's.

During live games, every few seconds it reads three things side by side:
- ESPN's public game summary (each play carries the wallclock time it happened), turned into stat lines with the
  same code our watcher uses (scoring/espn_feed.py) and scored with our engine and our saved league rules;
- OUR saved live lines (live_stat_lines, written by the game-day watcher on the e2-micro), scored the same way:
  this is what our server hands the app;
- Sleeper's public weekly stat lines, scored the same way.
Each time a player's points change in ESPN's feed, it times when that change first shows in each source against the
play's own wallclock. A source "has" the change once its number crosses the halfway point between the old and new
value, so small rule-by-rule differences between feeds (first downs, etc.) don't hide a match.

It also records what it costs to run: bytes per source per hour, how big the week's saved rows are (what each server
instance reads every 10 s), and how often the watcher saves.

Prints numbers only (the repo and its Actions logs are public): no player, team or owner names, no ids.

    python -m scoring.speed_test --wait 60 --minutes 210
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import time
from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Any

import requests

from scoring import espn_feed
from scoring.engine import player_points
from scoring.shadow import POSITIONS as SLEEPER_POSITIONS

ESPN = espn_feed.ESPN
SLEEPER = "https://api.sleeper.app"
LEGACY_SLEEPER_ID = "1361083066197491712"
KEEP = {"QB", "RB", "WR", "TE", "K"}
BIG = 4.0  # a points change this big is a touchdown-sized play


class Meter:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "legacy-dynasty-speed-test"
        self.bytes: dict[str, int] = defaultdict(int)
        self.calls: dict[str, int] = defaultdict(int)

    def get(self, name: str, url: str, timeout: int = 15) -> Any:
        try:
            resp = self.session.get(url, timeout=timeout)
            self.bytes[name] += len(resp.content)
            self.calls[name] += 1
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            print(f"[speed] {name} fetch failed: {type(exc).__name__}", flush=True)
            return None


def slim(lines: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Same trim the watcher applies before saving (scripts/live_watcher.slim)."""
    out = {}
    for pid, line in lines.items():
        if line.get("pos") not in KEEP:
            continue
        stats = {k: v for k, v in (line.get("stats") or {}).items() if v and not k.endswith("_lng") and not k.startswith("idp_")}
        if stats:
            out[pid] = {"stats": stats, "pos": line["pos"]}
    return out


def scored(lines: dict[str, dict[str, Any]], scoring: dict[str, Any]) -> dict[str, float]:
    return {pid: player_points(l.get("stats"), scoring, l.get("pos")) for pid, l in lines.items() if l.get("pos") in KEEP}


class History:
    """player -> [(time, points)], appended only on change. Before a player's first entry they had 0."""

    def __init__(self, start: float) -> None:
        self.start = start
        self.h: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self.began: float | None = None  # first successful read

    def update(self, t: float, values: dict[str, float], players: set[str] | None = None) -> None:
        if self.began is None:
            self.began = t
        for pid in (players if players is not None else set(values) | set(self.h)):
            v = round(values.get(pid, 0.0), 2)
            seq = self.h[pid]
            if not seq or abs(seq[-1][1] - v) > 0.001:
                seq.append((t, v))

    def at(self, pid: str, t: float) -> float | None:
        if self.began is None or t < self.began:
            return None
        v = 0.0
        for when, val in self.h.get(pid, []):
            if when > t:
                break
            v = val
        return v

    def crossed(self, pid: str, since: float, old: float, new: float) -> float | None:
        """First time after `since` this source's value passed halfway from old to new (None if it never did, or it
        was already past halfway at `since`)."""
        mid = (old + new) / 2
        up = new > old
        before = self.at(pid, since)
        if before is None or (before >= mid if up else before <= mid):
            return None
        for when, val in self.h.get(pid, []):
            if when > since and (val >= mid if up else val <= mid):
                return when
        return None


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0.0


def summary_line(name: str, lags: list[float], missed: int) -> str:
    if not lags:
        return f"  {name}: none timed ({missed} never showed)"
    return (f"  {name}: {len(lags)} timed; median {median(lags):.0f}s, 90th pct {pct(lags, 0.9):.0f}s, "
            f"worst {max(lags):.0f}s, under 80s {sum(1 for x in lags if x <= 80) * 100 // len(lags)}%; {missed} never showed")


def load_scoring(client: Any) -> tuple[dict[str, Any], str]:
    from league_ai.settings_store import load_settings
    leagues = client.table("leagues").select("id, sleeper_league_id").execute().data or []
    leagues.sort(key=lambda lg: str(lg.get("sleeper_league_id")) != LEGACY_SLEEPER_ID)
    for lg in leagues:
        scoring = load_settings(client, str(lg["id"])).scoring_settings or {}
        if scoring:
            return {k: v for k, v in scoring.items() if isinstance(v, (int, float)) and v}, "saved league rules"
    sid = LEGACY_SLEEPER_ID
    info = requests.get(f"{SLEEPER}/v1/league/{sid}", timeout=15).json() or {}
    return {k: v for k, v in (info.get("scoring_settings") or {}).items() if v}, "Sleeper rules (none saved)"


def run(wait: float, minutes: float, every: float, smoke: bool = False) -> tuple[list[str], dict[str, Any]]:
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    scoring, rules_from = load_scoring(client)
    meter = Meter()
    players = meter.get("setup", f"{SLEEPER}/v1/players/nfl", timeout=60) or {}
    try:
        from pipeline.nfl_data.common import fetch_text
        from pipeline.nfl_data.crosswalk import CROSSWALK_URL, Crosswalk
        crosswalk = Crosswalk.from_csv(fetch_text(CROSSWALK_URL))
    except Exception:
        crosswalk = None
    idmap = espn_feed.IdMap(players, crosswalk)
    del players

    # Wait for kickoff.
    deadline = time.time() + wait * 60
    while True:
        board = meter.get("espn", f"{ESPN}/scoreboard") or {}
        live = [g for g in espn_feed.games(board) if g["state"] == "in" or (smoke and g["state"] == "post")]
        if live or time.time() > deadline:
            break
        time.sleep(30)
    season = int(((board.get("season") or {}).get("year")) or 0)
    week = int(((board.get("week") or {}).get("number")) or 0)
    out = [f"speed test: season {season} week {week}, scored with {rules_from} ({len(scoring)} scored stats), "
           f"{len(live)} live game(s) at start, polling every {every:g}s"]
    if not live:
        out.append("  no live game started inside the wait window; nothing to time")
        return out, {}

    # What every server instance reads every 10 s during games: the whole week's saved rows (Sunday's are biggest).
    week_rows = client.table("live_stat_lines").select("game_id,lines,games,source,updated_at").eq("season", season).eq("week", week).execute().data or []
    week_bytes = len(json.dumps(week_rows))
    out.append(f"  saved live rows this week: {len(week_rows)} rows, {week_bytes / 1e3:.0f} KB as JSON (one server read)")
    del week_rows

    start = time.time()
    espn_h, ours_h, sleeper_h = History(start), History(start), History(start)
    events: list[dict[str, Any]] = []
    seen_play: set[str] = set()
    espn_prev: dict[str, float] | None = None
    espn_games: dict[str, dict[str, Any]] = {}
    saves: dict[str, str] = {}
    save_count = 0
    heartbeat_ages: list[float] = []
    loops = 0
    cpu0 = time.process_time()
    end = start + minutes * 60
    ended_at: float | None = None
    query = "season_type=regular&" + "&".join(f"position[]={p}" for p in SLEEPER_POSITIONS)
    while time.time() < end:
        began = time.time()
        loops += 1
        board = meter.get("espn", f"{ESPN}/scoreboard") or board
        games = espn_feed.games(board)
        live_ids = [g["id"] for g in games if g["state"] == "in" or (smoke and g["state"] == "post")][: 2 if smoke else None]
        tracked = [g["id"] for g in games if g["id"] in live_ids or g["id"] in saves]
        if not live_ids:
            ended_at = ended_at or time.time()
            if time.time() - ended_at > 600:  # 10 minutes after the last whistle, for late feeds to settle
                break

        # 1) ESPN, the play-by-play source: new plays and the points they moved.
        newest_wall: float | None = None
        t_espn = time.time()
        for gid in live_ids:
            summ = meter.get("espn", f"{ESPN}/summary?event={gid}")
            if not summ:
                continue
            lines, _ = espn_feed.week_lines([summ], idmap)
            espn_games[gid] = slim(lines)  # a game that ends keeps its last lines
            for drive in list(((summ.get("drives") or {}).get("previous") or [])) + [((summ.get("drives") or {}).get("current") or {})]:
                for play in drive.get("plays") or []:
                    pid = str(play.get("id") or "")
                    if not pid or pid in seen_play:
                        continue
                    seen_play.add(pid)
                    if loops == 1:
                        continue
                    try:
                        wall = datetime.fromisoformat(str(play.get("wallclock")).replace("Z", "+00:00")).timestamp()
                    except (TypeError, ValueError):
                        continue
                    newest_wall = max(newest_wall or 0, wall)
        espn_pts = scored({pid: l for g in espn_games.values() for pid, l in g.items()}, scoring)
        if espn_prev is not None and newest_wall:
            for pid in set(espn_pts) | set(espn_prev):
                old, new = espn_prev.get(pid, 0.0), espn_pts.get(pid, 0.0)
                if abs(new - old) >= 0.01:
                    events.append({"pid": pid, "old": old, "new": new, "wall": newest_wall, "espn": t_espn})
        if live_ids:
            espn_h.update(t_espn, espn_pts)
            espn_prev = espn_pts

        # 2) Ours: the rows the watcher saved, as the server reads them.
        t_ours = time.time()
        try:
            rows = (client.table("live_stat_lines").select("game_id,lines,updated_at").eq("season", season).eq("week", week)
                    .in_("game_id", tracked + ["_watcher"]).execute().data or [])
            meter.bytes["supabase"] += len(json.dumps(rows))
            meter.calls["supabase"] += 1
        except Exception as exc:
            print(f"[speed] supabase read failed: {type(exc).__name__}", flush=True)
            rows = None
        if rows is not None:
            our_lines: dict[str, dict[str, Any]] = {}
            for row in rows:
                gid, stamp = str(row.get("game_id")), str(row.get("updated_at") or "")
                if gid == "_watcher":
                    continue
                if gid in live_ids or gid in saves:
                    if saves.get(gid) not in (None, stamp):
                        save_count += 1
                    saves[gid] = stamp
                our_lines.update(row.get("lines") or {})
            newest = max((str(r.get("updated_at") or "") for r in rows), default="")
            try:
                heartbeat_ages.append(t_ours - datetime.fromisoformat(newest.replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
            ours_h.update(t_ours, scored(our_lines, scoring))

        # 3) Sleeper's public stat lines (every player in the week; only changes matter).
        t_sl = time.time()
        sl_rows = meter.get("sleeper", f"{SLEEPER}/stats/nfl/{season}/{week}?{query}", timeout=20)
        if sl_rows is not None:
            sl_lines = {}
            for row in sl_rows:
                pid = str(row.get("player_id") or "")
                pos = str((row.get("player") or {}).get("position") or row.get("position") or "").upper()
                if pid and pos in KEEP:
                    sl_lines[pid] = {"stats": row.get("stats") or {}, "pos": pos}
            sl_pts = scored(sl_lines, scoring)
            sleeper_h.update(t_sl, sl_pts)
        time.sleep(max(0.0, every - (time.time() - began)))

    # Time every ESPN points change in each source against the play's wallclock.
    lag = {"espn": [], "ours": [], "sleeper": []}
    lag_big = {"espn": [], "ours": [], "sleeper": []}
    missed = {"ours": 0, "sleeper": 0}
    missed_big = {"ours": 0, "sleeper": 0}
    ours_vs_espn: list[float] = []
    sleeper_known = 0
    rows_out = []
    for e in events:
        big = abs(e["new"] - e["old"]) >= BIG
        d_espn = e["espn"] - e["wall"]
        if d_espn < 0 or d_espn > 1800:
            continue  # clock oddities
        lag["espn"].append(d_espn)
        if big:
            lag_big["espn"].append(d_espn)
        row = {"delta": round(e["new"] - e["old"], 2), "espn": round(d_espn, 1)}
        since = e["wall"] - 1
        for name, hist in (("ours", ours_h), ("sleeper", sleeper_h)):
            if hist.at(e["pid"], since) is None:
                continue
            hit = hist.crossed(e["pid"], since, e["old"], e["new"])
            if name == "sleeper":
                sleeper_known += 1
            if hit is None:
                missed[name] += 1
                if big:
                    missed_big[name] += 1
                row[name] = None
                continue
            d = hit - e["wall"]
            lag[name].append(d)
            if big:
                lag_big[name].append(d)
            row[name] = round(d, 1)
            if name == "ours":
                ours_vs_espn.append(hit - e["espn"])
        rows_out.append(row)

    elapsed_h = max((time.time() - start) / 3600, 1e-9)
    out.append(f"  ran {elapsed_h * 60:.0f} min, {loops} polls, {len(events)} player points changes in ESPN's feed")
    out.append("All points changes (seconds after the real play):")
    out.append(summary_line("ESPN feed", lag["espn"], 0))
    out.append(summary_line("OUR scores (watcher -> saved lines)", lag["ours"], missed["ours"]))
    out.append(summary_line("Sleeper", lag["sleeper"], missed["sleeper"]))
    out.append(f"Touchdown-sized changes ({BIG:g}+ pts):")
    out.append(summary_line("ESPN feed", lag_big["espn"], 0))
    out.append(summary_line("OUR scores", lag_big["ours"], missed_big["ours"]))
    out.append(summary_line("Sleeper", lag_big["sleeper"], missed_big["sleeper"]))
    if ours_vs_espn:
        out.append(f"  ours vs this test's own ESPN read: median {median(ours_vs_espn):+.0f}s, 90th pct {pct(ours_vs_espn, 0.9):+.0f}s "
                   f"(our watcher + database step; negative = watcher saw it first)")
    last_espn = {pid: hist[-1][1] for pid, hist in espn_h.h.items() if hist}
    last_ours = {pid: hist[-1][1] for pid, hist in ours_h.h.items() if hist}
    both = [pid for pid in last_espn if pid in last_ours and (last_espn[pid] or last_ours[pid])]
    same = sum(1 for pid in both if abs(last_espn[pid] - last_ours[pid]) < 0.02)
    out.append(f"  at the end, our saved points equal ESPN-derived points for {same}/{len(both)} players")
    if heartbeat_ages:
        out.append(f"  newest saved row age while live: median {median(heartbeat_ages):.0f}s, worst {max(heartbeat_ages):.0f}s")
    out.append(f"  watcher saves of the tracked game rows: {save_count} ({save_count / elapsed_h:.0f}/hour)")
    out.append("Cost of this test's reads (per hour):")
    for name in ("espn", "sleeper", "supabase"):
        out.append(f"  {name}: {meter.bytes[name] / 1e6 / elapsed_h:.0f} MB/hour over {meter.calls[name]} calls")
    cpu = time.process_time() - cpu0
    out.append(f"  CPU {cpu / elapsed_h:.0f} s/hour; peak memory {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")
    data = {"season": season, "week": week, "rules": rules_from, "events": rows_out, "week_rows_kb": round(week_bytes / 1e3),
            "saves_per_hour": round(save_count / elapsed_h), "hours": round(elapsed_h, 2),
            "bytes_per_hour": {k: round(v / elapsed_h) for k, v in meter.bytes.items()}}
    return out, data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=60, help="minutes to wait for a game to go live")
    ap.add_argument("--minutes", type=float, default=210)
    ap.add_argument("--every", type=float, default=3)
    ap.add_argument("--smoke", action="store_true", help="treat finished games as live (a quick check of every read)")
    ap.add_argument("--out", default="speed-test.json")
    args = ap.parse_args()
    lines, data = run(args.wait, args.minutes, args.every, args.smoke)
    for line in lines:
        print(line, flush=True)
    with open(args.out, "w") as fh:
        json.dump(data, fh)


if __name__ == "__main__":
    main()
