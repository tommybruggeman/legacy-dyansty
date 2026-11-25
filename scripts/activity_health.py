"""Is Owner activity catching every owner? A names-free check run by League AI checks (check: activity_health).

For each member of the league (Owner 1, 2, ... in a fixed but meaningless order, never names or emails) it prints:
rows saved in the last N days in THIS league vs under another league id or none (rows the console can't see),
sessions, kinds, app builds, the last sign-in, the last saved row, and when the phone last checked in for alerts.
An owner who signs in but has few rows here points at a tracking gap rather than not using the app.

    python -m scripts.activity_health --days 14
"""
from __future__ import annotations

import argparse
import hashlib
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from supabase import create_client

from api import usage_stats

LEGACY_SLEEPER_ID = "1361083066197491712"


def _all(query, size: int = 1000) -> list[dict]:
    rows, start = [], 0
    while True:
        page = query.range(start, start + size - 1).execute().data or []
        rows += page
        if len(page) < size:
            return rows
        start += size


def day(v) -> str:
    return str(v or "")[:16].replace("T", " ") or "-"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()

    seasons = client.table("league_seasons").select("league_id,season").eq("sleeper_league_id", LEGACY_SLEEPER_ID).execute().data or []
    league_ids = {s["league_id"] for s in seasons}
    all_seasons = client.table("league_seasons").select("league_id,season").execute().data or []
    season_of = {s["league_id"]: s.get("season") for s in all_seasons}
    members = _all(client.table("league_memberships").select("user_id,league_id,role").in_("league_id", list(league_ids)))
    users = sorted({m["user_id"] for m in members}, key=lambda u: hashlib.sha256(u.encode()).hexdigest())
    print(f"League ids for this league: {len(league_ids)} ({', '.join(str(season_of.get(x)) for x in league_ids)})")

    # every membership each owner has (any league), to spot owners whose app resolves to another league
    other = _all(client.table("league_memberships").select("user_id,league_id").in_("user_id", users))
    leagues_of = defaultdict(set)
    for m in other:
        leagues_of[m["user_id"]].add(m["league_id"])

    rows = _all(client.table("app_screen_views").select("user_id,league_id,screen,seconds,viewed_at,source,kind,build")
                .in_("user_id", users).gte("viewed_at", since).order("id"))
    by_user = defaultdict(list)
    for r in rows:
        by_user[r["user_id"]].append(r)

    signed = {}
    try:
        page = 1
        while True:
            got = client.auth.admin.list_users(page=page, per_page=200)
            if not got:
                break
            for u in got:
                signed[str(u.id)] = getattr(u, "last_sign_in_at", None)
            if len(got) < 200:
                break
            page += 1
    except Exception as exc:
        print(f"(sign-in times unavailable: {type(exc).__name__})")
    try:
        tokens = _all(client.table("push_tokens").select("user_id,updated_at,platform").in_("user_id", users))
    except Exception:
        tokens = []
    token_at = defaultdict(str)
    for t in tokens:
        token_at[t["user_id"]] = max(token_at[t["user_id"]], str(t.get("updated_at") or ""))

    print(f"Last {args.days} days. 'here' = rows the console can see; 'elsewhere' = saved under another league id; 'none' = no league id.\n")
    for i, uid in enumerate(users, 1):
        rs = by_user.get(uid, [])
        here = [r for r in rs if r.get("league_id") in league_ids]
        elsewhere = [r for r in rs if r.get("league_id") and r.get("league_id") not in league_ids]
        none = [r for r in rs if not r.get("league_id")]
        app_here = [r for r in here if (r.get("source") or "app") == "app"]
        sess_here = [s for s in usage_stats.sessions(here) if s["source"] == "app"]
        sess_all = [s for s in usage_stats.sessions(rs) if s["source"] == "app"]
        kinds = Counter(r.get("kind") or "screen" for r in app_here)
        builds = sorted({str(r.get("build") or "?") for r in rs if (r.get("source") or "app") == "app"})
        opens = sum(1 for r in rs if r.get("kind") == "app_open")
        flag = "  <-- few sessions" if len(sess_here) <= 1 else ""
        print(f"Owner {i}: rows here {len(here)}, elsewhere {len(elsewhere)} (seasons {sorted({str(season_of.get(r['league_id'])) for r in elsewhere})}), "
              f"none {len(none)} | app sessions here {len(sess_here)}, counting all rows {len(sess_all)} | app opens {opens}{flag}")
        print(f"   builds {builds} | kinds {dict(kinds.most_common(6))} | memberships {len(leagues_of[uid])}")
        print(f"   last sign-in {day(signed.get(uid))} | last row {day(max((r['viewed_at'] for r in rs), default=''))} | "
              f"alert token checked in {day(token_at.get(uid))}")


if __name__ == "__main__":
    main()
