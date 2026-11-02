"""Game-day watcher: keeps every owner's lock screen Live Activity current while games are on.

Every INTERVAL seconds it reads the running activities (live_activities, saved by the app when it starts
one), builds each matchup's box score with the same code as the app's game screen (api.box_score), and
pushes the new numbers straight to Apple (api.apns) when anything on the widget changed. When both
lineups are done for the week it sends a final "end" push. Tokens Apple says are gone are retired.

Runs on GitHub Actions during NFL game windows (.github/workflows/live-activity.yml), which is free for
this public repo. It is a plain loop, so it can move to any always-on box later without changes.
Prints counts and status codes only (the repo and its Actions logs are public).

Environment: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, APNS_KEY_P8, APNS_KEY_ID (see api/apns.py),
MAX_MINUTES (default 350), INTERVAL (seconds, default 15).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import apns, box_score, cache, league, live_activity, own_league  # noqa: E402

APPLE_LIMIT = timedelta(hours=8)  # Apple ends a Live Activity after 8 hours on screen
IDLE_SLEEP = 60
END_DISMISS = 30 * 60  # a finished matchup stays on the lock screen half an hour
STALE_AFTER = 15 * 60  # if updates stop (watcher down), iOS greys the widget after 15 minutes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def league_ctx(client: Any, league_id: str) -> dict[str, Any]:
    """What box_score.game needs for one league (like the server's _season_context, without an owner)."""
    from season_engine import SeasonResolver

    def load() -> dict[str, Any]:
        season = SeasonResolver(client).get_active_season(league_id)
        sid = season.sleeper_league_id
        try:
            own_league.sync(client, league_id, sid, int(season.season))
        except Exception:
            pass
        return {
            "league_id": league_id,
            "season": int(season.season),
            "sleeper_id": sid,
            "names": league.roster_names(client, league_id, sid),
            "slots": (league.sleeper.get(f"league/{sid}") or {}).get("roster_positions") or [],
        }

    return cache.cached(("watch_ctx", league_id), 600, load)


def team_rows(client: Any, league_id: str) -> list[dict[str, Any]]:
    from api import teams
    return cache.cached(("watch_teams", league_id), 600, lambda: teams.league_teams(client, league_id))


def week_done(result: dict[str, Any]) -> bool:
    """Every starter on both sides is final (or on a bye / an empty slot): nothing left to watch this week."""
    if result.get("is_final"):
        return True
    return all(p.get("status") not in ("waiting", "playing") for side in result.get("teams") or [] for p in side.get("players") or [])


class Watcher:
    def __init__(self, client: Any, push: Any):
        self.client = client
        self.push = push
        self.before: dict[tuple, dict[str, float]] = {}  # matchup -> starter points at the last check
        self.shown: dict[str, dict[str, Any]] = {}  # activity id -> props last sent
        self.last: dict[str, dict[str, Any]] = {}  # activity id -> latest points change shown
        self.counts = {"updates": 0, "ends": 0, "gone": 0, "errors": 0}

    def active(self) -> list[dict[str, Any]]:
        rows = (self.client.table("live_activities").select("activity_id,league_id,roster_id,week,matchup_id,push_token,started_at")
                .is_("ended_at", "null").execute().data or [])
        return rows

    def retire(self, activity_id: str) -> None:
        self.client.table("live_activities").update({"ended_at": _now().isoformat()}).eq("activity_id", activity_id).execute()
        self.shown.pop(activity_id, None)
        self.last.pop(activity_id, None)

    def send(self, row: dict[str, Any], event: str, props: dict[str, Any], priority: int) -> bool:
        payload = live_activity.apns_payload(event, props, dismiss_after=END_DISMISS if event == "end" else None,
                                             stale_after=STALE_AFTER if event == "update" else None)
        result = self.push.send_live_activity(row["push_token"], payload, priority=priority)
        if result.ok:
            return True
        if result.gone:
            self.counts["gone"] += 1
            self.retire(row["activity_id"])
        else:
            self.counts["errors"] += 1
            print(f"APNS {result.status} {result.reason}", flush=True)
        return False

    def tick(self) -> int:
        """One pass over every running activity; returns how many there were."""
        rows = self.active()
        now = _now()
        groups: dict[tuple, list[dict[str, Any]]] = {}
        for row in rows:
            started = _parse(row.get("started_at"))
            if started and now - started > APPLE_LIMIT + timedelta(minutes=10):
                self.retire(row["activity_id"])  # Apple already took it off the lock screen
                continue
            groups.setdefault((row["league_id"], int(row["week"]), int(row["matchup_id"])), []).append(row)
        for key, members in groups.items():
            league_id, week, matchup_id = key
            try:
                ctx = league_ctx(self.client, league_id)
                result = box_score.game(self.client, ctx, week, matchup_id, team_rows(self.client, league_id))
            except Exception as exc:
                self.counts["errors"] += 1
                print(f"WATCH_GAME_ERROR {type(exc).__name__}", flush=True)
                continue
            if result is None:
                continue
            done = week_done(result)
            before = self.before.get(key)
            for row in members:
                aid = row["activity_id"]
                props = live_activity.props(result, int(row["roster_id"]), before, self.last.get(aid))
                if props is None:
                    continue
                if props.get("last"):
                    self.last[aid] = props["last"]
                if done:
                    props["final"] = True
                    if self.send(row, "end", props, 10):
                        self.counts["ends"] += 1
                        self.retire(aid)
                    continue
                old = self.shown.get(aid)
                if not live_activity.changed(old, props):
                    continue
                score_moved = old is None or old.get("me") != props["me"] or old.get("them") != props["them"]
                if self.send(row, "update", props, 10 if score_moved else 5):
                    self.counts["updates"] += 1
                    self.shown[aid] = props
            self.before[key] = live_activity.player_points(result)
        return len(rows)


def main() -> int:
    from supabase import create_client

    if not apns.configured():
        print("APNs key not set (APNS_KEY_P8 / APNS_KEY_ID): nothing to send.", flush=True)
        return 0
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    push = apns.Client.from_env()
    watcher = Watcher(client, push)
    stop = time.time() + float(os.getenv("MAX_MINUTES", "350")) * 60
    interval = float(os.getenv("INTERVAL", "15"))
    passes = 0
    try:
        while time.time() < stop:
            started = time.time()
            try:
                running = watcher.tick()
            except Exception as exc:
                watcher.counts["errors"] += 1
                print(f"WATCH_TICK_ERROR {type(exc).__name__}", flush=True)
                running = 0
            passes += 1
            if passes % 40 == 0:
                print(f"pass {passes}: {running} running, {watcher.counts}", flush=True)
            wait = (interval if running else IDLE_SLEEP) - (time.time() - started)
            if wait > 0:
                time.sleep(min(wait, max(0.0, stop - time.time())))
    finally:
        push.close()
    print(f"done after {passes} passes: {watcher.counts}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
