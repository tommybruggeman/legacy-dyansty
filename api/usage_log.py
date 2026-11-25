"""Owner activity in our own table (app_screen_views): which pages owners open, how long they stay,
and what they do there. No outside analytics service.

Kinds of rows:
- screen: a page view. The app times each screen; the website logs each page change.
- action: something an owner did (set a lineup, sent a trade, asked the GM). The server logs these
  itself from successful POST/DELETE requests, so they need no app update.
- tap / swipe: every touch in the app, as a spot on the screen (fractions of width/height) on that screen.
- app_open / app_close: the app coming to the front (with device, OS, build, time zone) and leaving it.
- push_open: an owner opened the app from an alert (sent by the app).
- frame: one small replay picture of the screen; detail.path points at it in the private 'replays' bucket.
- error: a crash or screen error (also printed to the server log).

api/usage_stats.py turns the rows into the commissioner's Activity page (app and website);
scripts/app_usage_report.py prints a names-free summary for the public Actions log.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

# Inserts never ask for the new rows back: owners may add rows but only the league owner's account may read
# them (owner-activity.sql), and asking for them back needs read access, so every other owner's insert failed.
MINIMAL = "minimal"

MAX_SECONDS = 30 * 60  # a screen left open longer than this counts as 30 minutes
MAX_VIEWS = 400
KINDS = ("screen", "action", "tap", "swipe", "push_open", "app_open", "app_close", "error", "frame", "load", "rage", "search", "outbound", "refresh", "feedback", "fail")
MAX_DETAIL = 600  # characters of JSON per event (tap spots, device info)

# Requests that are plumbing, not something the owner chose to do.
SKIP_ACTIONS = ("/me/activity", "/me/feedback", "/me/crash", "/me/push-token", "/me/live-activity", "/front-office/chat/resume", "/commissioner/activity")

# Ids in paths become placeholders, so /player/4046 and /player/6794 count as one screen.
_ID = re.compile(r"/(?:[0-9a-f]{8}-[0-9a-f-]{27,}|\d+)(?=/|$)")


def screen_name(path: str) -> str:
    path = (path or "").split("?")[0].strip() or "/"
    path = _ID.sub("/[id]", path)
    return path[:120]


def _detail(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for k, v in list(value.items())[:20]:
        if isinstance(v, (int, float, bool)) or v is None:
            out[str(k)[:30]] = v
        else:
            out[str(k)[:30]] = str(v)[:120]
    return out if len(json.dumps(out)) <= MAX_DETAIL else None


def rows(*, user_id: str, league_id: str | None, views: list[dict[str, Any]], build: str | None, source: str) -> list[dict[str, Any]]:
    out = []
    now = datetime.now(timezone.utc).isoformat()
    for v in views[:MAX_VIEWS]:
        try:
            seconds = int(v.get("seconds") or 0)
        except (TypeError, ValueError):
            continue
        if seconds < 0:
            continue
        at = str(v.get("at") or "")[:40] or now
        kind = str(v.get("kind") or "screen")
        if kind not in KINDS:
            continue
        row = {"user_id": user_id, "league_id": league_id, "screen": screen_name(str(v.get("screen") or "")),
               "seconds": min(seconds, MAX_SECONDS), "viewed_at": at, "build": (build or "")[:20] or None, "source": source}
        # Every row has the same keys: a bulk insert needs that. (Screen views carry attention and which page.)
        row["kind"] = kind
        row["detail"] = _detail(v.get("detail"))
        out.append(row)
    return out


def save(client: Any, *, user_id: str, league_id: str | None, views: list[dict[str, Any]], build: str | None, source: str) -> int:
    """Insert the views as the signed-in owner. Never fails the request: tracking is a nice-to-have."""
    batch = rows(user_id=user_id, league_id=league_id, views=views, build=build, source=source)
    if not batch:
        return 0
    try:
        client.table("app_screen_views").insert(batch, returning=MINIMAL).execute()
    except Exception as exc:
        print(f"ACTIVITY_SAVE_FAILED {type(exc).__name__} rows={len(batch)}", flush=True)
        return 0
    return len(batch)


def action_name(method: str, path: str) -> str | None:
    """'POST /trade-offers/9f.../accept' -> 'POST /trade-offers/[id]/accept'; None for plumbing requests."""
    method = (method or "").upper()
    if method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    path = screen_name(path)
    if any(path.startswith(p) for p in SKIP_ACTIONS):
        return None
    name = path if method == "POST" else f"{method} {path}"
    return name[:120]


def save_action(client: Any, *, user_id: str, league_id: str | None, method: str, path: str, build: str | None = None) -> bool:
    """One 'action' row for a successful request. Never raises."""
    name = action_name(method, path)
    if not name:
        return False
    try:
        client.table("app_screen_views").insert({"user_id": user_id, "league_id": league_id, "screen": name, "seconds": 0,
                                                 "kind": "action", "build": (build or "")[:20] or None, "source": "app"}, returning=MINIMAL).execute()
    except Exception:
        return False
    return True


def save_error(client: Any, *, user_id: str, league_id: str | None, screen: str | None, build: str | None) -> bool:
    """One 'error' row (screen only; the message stays in the server log). Never raises."""
    try:
        client.table("app_screen_views").insert({"user_id": user_id, "league_id": league_id, "screen": screen_name(screen or "/"),
                                                 "seconds": 0, "kind": "error", "build": (build or "")[:20] or None, "source": "app"}, returning=MINIMAL).execute()
    except Exception:
        return False
    return True
