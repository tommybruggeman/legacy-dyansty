"""Screen time: which pages owners open and how long they stay, in our own table (app_screen_views).

The app batches views and sends them every minute or when it goes to the background; the website
logs a view per page load. scripts/app_usage_report.py turns it into the usage report.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

MAX_SECONDS = 30 * 60  # a screen left open longer than this counts as 30 minutes
MAX_VIEWS = 200

# Ids in paths become placeholders, so /player/4046 and /player/6794 count as one screen.
_ID = re.compile(r"/(?:[0-9a-f]{8}-[0-9a-f-]{27,}|\d+)(?=/|$)")


def screen_name(path: str) -> str:
    path = (path or "").split("?")[0].strip() or "/"
    path = _ID.sub("/[id]", path)
    return path[:120]


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
        out.append({"user_id": user_id, "league_id": league_id, "screen": screen_name(str(v.get("screen") or "")),
                    "seconds": min(seconds, MAX_SECONDS), "viewed_at": at, "build": (build or "")[:20] or None, "source": source})
    return out


def save(client: Any, *, user_id: str, league_id: str | None, views: list[dict[str, Any]], build: str | None, source: str) -> int:
    """Insert the views as the signed-in owner. Never fails the request: tracking is a nice-to-have."""
    batch = rows(user_id=user_id, league_id=league_id, views=views, build=build, source=source)
    if not batch:
        return 0
    try:
        client.table("app_screen_views").insert(batch).execute()
    except Exception:
        return 0
    return len(batch)
