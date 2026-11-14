"""Where owners spend their time in the app and on the website, from our own app_screen_views table.

Run by League AI checks (check: app_usage). The repo and its Actions logs are public, so this prints
numbers only: screens by total time, opens and owners who used them; owners appear as Owner 1, 2, ...
ordered by time spent, never by name or email.

    python -m scripts.app_usage_report --days 7
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from supabase import create_client

# Friendlier names for the app's routes (anything not listed prints as its route).
NAMES = {
    "/": "Home", "/my-team": "My Team", "/front-office": "Front Office (GM)", "/league": "League",
    "/market": "Market", "/settings": "Settings", "/player/[id]": "Player page", "/player-games/[id]": "Player game log",
    "/team/[id]": "Another team", "/game/[id]/[id]": "Box score", "/search": "Search", "/chats": "Past GM chats",
    "/report-card": "GM report card", "/league-cap": "League cap", "/activity": "League activity", "/transactions": "Transactions",
    "/trade/[id]": "Trade builder", "/bid/[id]": "Waiver bid", "/ir": "IR", "/profile": "Profile", "/league-rules": "League rules",
    "/commissioner": "Commissioner tools", "/delete-account": "Delete account",
}


def _all(query, size: int = 1000) -> list[dict]:
    rows, start = [], 0
    while True:
        page = query.range(start, start + size - 1).execute().data or []
        rows += page
        if len(page) < size:
            return rows
        start += size


def minutes(seconds: float) -> str:
    return f"{seconds / 60:,.0f} min" if seconds >= 60 else f"{seconds:,.0f} s"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=int(os.getenv("USAGE_DAYS") or 7))
    args = parser.parse_args()
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()

    try:
        views = _all(client.table("app_screen_views").select("user_id,screen,seconds,viewed_at,source").gte("viewed_at", since).order("id"))
    except Exception as exc:
        print(f"[usage] can't read app_screen_views yet ({type(exc).__name__}). Has the App Store SQL been run?")
        return 1
    print(f"[usage] last {args.days} days: {len(views)} screen views")
    if not views:
        print("[usage] nothing recorded yet (needs build 86+ on phones, or the website after this deploy)")
        return 0

    by_screen: dict[tuple[str, str], dict] = defaultdict(lambda: {"secs": 0, "opens": 0, "users": set()})
    by_user: dict[str, dict] = defaultdict(lambda: {"secs": 0, "opens": 0, "days": set(), "app": 0, "web": 0})
    by_hour: dict[int, int] = defaultdict(int)
    for v in views:
        src = v.get("source") or "app"
        name = NAMES.get(v["screen"], v["screen"]) if src == "app" else v["screen"]
        s = by_screen[(src, name)]
        s["secs"] += v["seconds"] or 0
        s["opens"] += 1
        s["users"].add(v["user_id"])
        u = by_user[v["user_id"]]
        u["secs"] += v["seconds"] or 0
        u["opens"] += 1
        u["days"].add(str(v["viewed_at"])[:10])
        u["app" if src == "app" else "web"] += 1
        try:
            hour = (datetime.fromisoformat(str(v["viewed_at"]).replace("Z", "+00:00")) - timedelta(hours=7)).hour  # Mountain time
            by_hour[hour] += 1
        except ValueError:
            pass

    owners = len(by_user)
    print(f"[usage] {owners} owners active")
    for src in ("app", "website"):
        rows = sorted(((k[1], v) for k, v in by_screen.items() if k[0] == src), key=lambda kv: (-kv[1]["secs"], -kv[1]["opens"]))
        if not rows:
            continue
        total = sum(v["secs"] for _, v in rows) or 1
        print(f"\n[usage] {src.upper()} screens by time spent")
        print(f"{'screen':28} {'time':>9} {'share':>6} {'opens':>6} {'owners':>7} {'avg/open':>9}")
        for name, v in rows:
            avg = v["secs"] / v["opens"] if v["opens"] else 0
            print(f"{name[:28]:28} {minutes(v['secs']):>9} {v['secs'] / total:>6.0%} {v['opens']:>6} {len(v['users']):>4}/{owners:<2} {minutes(avg):>9}")

    print("\n[usage] owners (numbered by time spent, no names)")
    print(f"{'owner':8} {'time':>9} {'opens':>6} {'days':>5} {'app':>5} {'web':>5}")
    for i, (_, u) in enumerate(sorted(by_user.items(), key=lambda kv: -kv[1]["secs"]), 1):
        print(f"Owner {i:<2} {minutes(u['secs']):>9} {u['opens']:>6} {len(u['days']):>5} {u['app']:>5} {u['web']:>5}")

    print("\n[usage] opens by hour (Mountain time)")
    for h in range(24):
        if by_hour.get(h):
            print(f"{h:02d}:00 {'#' * min(60, by_hour[h])} {by_hour[h]}")

    try:
        chats = _all(client.table("league_ai_usage").select("user_id").eq("feature", "chat").gte("created_at", since))
        flags = _all(client.table("league_ai_feedback").select("reason").gte("created_at", since))
        print(f"\n[usage] GM questions: {len(chats)} from {len({c['user_id'] for c in chats})} owners; answers reported: {len(flags)}"
              + (f" ({', '.join(f'{r} {sum(1 for f in flags if f["reason"] == r)}' for r in sorted({f['reason'] for f in flags}))})" if flags else ""))
    except Exception as exc:
        print(f"[usage] GM numbers unavailable ({type(exc).__name__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
