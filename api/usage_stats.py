"""The commissioner's Activity page: how owners use the app and the website, from our own app_screen_views
and league_ai_usage tables. Shared by the app (GET /commissioner/activity) and the website (Settings >
League tools > Owner activity), so both show the same numbers.

Everything is worked out live from the raw rows (kept for good: a 12-team league writes a few thousand a
week), so any range works and nothing waits on a nightly job.

Sessions: an owner's events on one platform with no gap over 30 minutes. Website pages don't report how
long they stayed open, so their time is the gap to the owner's next page in the same session.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

SESSION_GAP = timedelta(minutes=30)
DEFAULT_TZ = "America/Denver"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LANDING = {"/", "web/home", "web/app", "web/streamlit_app"}

# Friendly names for the app's routes and the website's pages (anything unlisted shows as-is).
SCREENS: dict[str, str] = {
    "/": "Home", "/my-team": "My Team", "/front-office": "Front Office (GM)", "/league": "League",
    "/market": "Market", "/settings": "Settings", "/player/[id]": "Player page", "/player-games/[id]": "Player game log",
    "/team/[id]": "Another team", "/game/[id]/[id]": "Box score", "/search": "Search", "/chats": "Past GM chats",
    "/report-card": "GM report card", "/league-cap": "League cap", "/activity": "League activity", "/transactions": "Transactions",
    "/trade/[id]": "Trade builder", "/bid/[id]": "Waiver bid", "/ir": "IR", "/profile": "Profile", "/league-rules": "League rules",
    "/prospects": "Prospect rankings", "/commissioner": "Commissioner tools", "/commish-team": "Commish: fix a team",
    "/commish-add": "Commish: add a player", "/commish-rules": "Commish: rules", "/owner-activity": "Commish: owner activity",
    "/delete-account": "Delete account",
}
# Screens every owner can reach: the "never opened" list checks these (commissioner-only pages left out).
OWNER_SCREENS = tuple(k for k in SCREENS if not k.startswith(("/commish", "/commissioner", "/owner-activity", "/delete-account")))

ACTIONS: dict[str, str] = {
    "/my-team/lineup": "Set a lineup", "/front-office/chat/stream": "Asked the GM", "/front-office/chat": "Asked the GM",
    "/front-office/chat/add": "Added to a GM chat", "/front-office/report": "Reported a GM answer",
    "/front-office/moves/[id]/accept": "Took a GM move", "/front-office/moves/[id]/dismiss": "Dismissed a GM move",
    "/trade-offers": "Sent a trade offer", "/trade-offers/[id]/accept": "Accepted a trade", "/trade-offers/[id]/decline": "Declined a trade",
    "/trade-offers/[id]/counter": "Countered a trade", "/trade-offers/[id]/cancel": "Cancelled a trade offer",
    "/waivers/bid": "Placed a waiver bid", "/waivers/bid/[id]/cancel": "Cancelled a bid", "/waivers/add": "Added a free agent",
    "/my-team/drop": "Dropped a player", "/my-team/ir": "Moved a player to IR", "DELETE /my-team/ir": "Took a player off IR",
    "/market/targets/[id]": "Starred a target", "DELETE /market/targets/[id]": "Unstarred a target", "/prospects/star": "Starred a prospect",
    "/home/decisions/[id]": "Answered a Home decision", "/me/alerts": "Changed alert settings", "/me/profile": "Edited profile",
    "/me/gm-prefs": "Changed GM settings", "/me/league": "Switched league", "/me/co-owner": "Invited a co-owner",
    "/commissioner/lineup": "Commish: set a lineup", "/commissioner/add": "Commish: added a player",
    "/commissioner/drop": "Commish: dropped a player", "/commissioner/contract": "Commish: fixed a contract",
    "/commissioner/rules": "Commish: changed rules", "/commissioner/invite": "Commish: invited an owner",
    "/commissioner/switch": "Commish: flipped the league switch",
}
OWNER_ACTIONS = tuple(dict.fromkeys(v for k, v in ACTIONS.items() if not k.startswith("/commissioner") and "Commish" not in v))


def screen_label(row: dict[str, Any]) -> str:
    name = str(row.get("screen") or "/")
    kind = row.get("kind") or "screen"
    if kind == "action":
        return ACTIONS.get(name, name)
    if kind == "push_open":
        return "Opened an alert"
    if kind in ("tap", "swipe"):
        d = row.get("detail") or {}
        where = SCREENS.get(name, name)
        return f"Tap on {where}" if kind == "tap" else f"Swipe {d.get('dir') or ''} on {where}".replace("  ", " ")
    if kind == "app_open":
        return "Opened the app" if (row.get("detail") or {}).get("cold") else "Came back to the app"
    if kind == "app_close":
        return "Left the app"
    if kind == "error":
        return f"Error on {SCREENS.get(name, name)}"
    if name.startswith("web/"):
        return "Web: " + name[4:].split("_", 1)[-1].replace("_", " ")
    return SCREENS.get(name, name)


def _when(value: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _round(x: float, n: int = 1) -> float:
    return round(float(x), n)


def sessions(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group events into sessions per owner and platform. Each event gets 'at' and 'secs' (time spent)."""
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        at = _when(r.get("viewed_at"))
        if at is None or not r.get("user_id"):
            continue
        by_key[(str(r["user_id"]), r.get("source") or "app")].append({**r, "at": at, "kind": r.get("kind") or "screen"})
    out: list[dict[str, Any]] = []
    for (user_id, source), events in by_key.items():
        events.sort(key=lambda e: e["at"])
        current: list[dict[str, Any]] = []
        end: datetime | None = None
        for e in events:
            if current and end is not None and e["at"] - end > SESSION_GAP:
                out.append(_session(user_id, source, current))
                current = []
            current.append(e)
            e_end = e["at"] + timedelta(seconds=int(e.get("seconds") or 0))
            end = max(end, e_end) if end and current[:-1] else e_end
        if current:
            out.append(_session(user_id, source, current))
    out.sort(key=lambda s: s["start"])
    return out


def _session(user_id: str, source: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    views = [e for e in events if e["kind"] == "screen"]
    for i, v in enumerate(views):
        secs = int(v.get("seconds") or 0)
        if secs == 0 and i + 1 < len(views):  # website pages: time until the next page
            secs = int(min((views[i + 1]["at"] - v["at"]).total_seconds(), 30 * 60))
        v["secs"] = max(secs, 0)
    for e in events:
        e.setdefault("secs", 0)
    start = events[0]["at"]
    end = max(e["at"] + timedelta(seconds=e["secs"]) for e in events)
    return {"user_id": user_id, "source": source, "start": start, "end": end, "seconds": (end - start).total_seconds(), "events": events}


def period_key(t: datetime, by: str) -> str:
    """'2026-11-23' (day), '2026-W48' (week, Monday start) or '2026-11' (month)."""
    if by == "week":
        y, w, _ = t.isocalendar()
        return f"{y}-W{w:02d}"
    if by == "month":
        return t.strftime("%Y-%m")
    return t.date().isoformat()


def build(rows: list[dict[str, Any]], *, owners: dict[str, dict[str, Any]], ai_rows: list[dict[str, Any]] | None = None,
          days: int = 30, tz: str | None = None, owner: str | None = None, now: datetime | None = None, by: str = "day") -> dict[str, Any]:
    """Every number on the Activity page. owners maps user_id -> {"name", "team"} for the league's members."""
    try:
        zone = ZoneInfo(tz or DEFAULT_TZ)
    except Exception:
        zone = ZoneInfo(DEFAULT_TZ)
    now = now or datetime.now(timezone.utc)
    ai_rows = ai_rows or []
    all_sessions = sessions(rows)
    events = [e for s in all_sessions for e in s["events"]]
    screens = [e for e in events if e["kind"] == "screen"]
    actions = [e for e in events if e["kind"] == "action"]

    def local(t: datetime) -> datetime:
        return t.astimezone(zone)

    def who(user_id: str) -> str:
        o = owners.get(user_id) or {}
        return o.get("name") or o.get("team") or "Former member"

    # --- headline numbers
    def active_since(delta: timedelta) -> int:
        return len({e["user_id"] for e in events if e["at"] >= now - delta})

    lengths = sorted(s["seconds"] for s in all_sessions)
    median = lengths[len(lengths) // 2] if lengths else 0
    day_count = max(1, days)
    totals = {
        "owners": len(owners), "active_today": len({e["user_id"] for e in events if local(e["at"]).date() == local(now).date()}),
        "active_7d": active_since(timedelta(days=7)), "active_30d": active_since(timedelta(days=30)),
        "sessions": len(all_sessions), "median_session_min": _round(median / 60), "minutes": _round(sum(lengths) / 60, 0),
        "sessions_per_owner_day": _round(len(all_sessions) / max(1, len({s["user_id"] for s in all_sessions})) / day_count, 2),
        "actions": len(actions), "gm_questions": sum(1 for a in ai_rows if a.get("feature") == "chat"),
        "gm_cost": _round(sum(float(a.get("cost_usd") or 0) for a in ai_rows), 2),
        "errors": sum(1 for e in events if e["kind"] == "error"), "push_opens": sum(1 for e in events if e["kind"] == "push_open"),
        "taps": sum(1 for e in events if e["kind"] == "tap"), "swipes": sum(1 for e in events if e["kind"] == "swipe"),
        "app_opens": sum(1 for e in events if e["kind"] == "app_open"),
    }

    # --- daily series
    daily: dict[str, dict[str, Any]] = {}
    for d in range(days - 1, -1, -1):
        key = (local(now) - timedelta(days=d)).date().isoformat()
        daily[key] = {"date": key, "owners": set(), "sessions": 0, "minutes": 0.0}
    for s in all_sessions:
        key = local(s["start"]).date().isoformat()
        if key in daily:
            daily[key]["owners"].add(s["user_id"])
            daily[key]["sessions"] += 1
            daily[key]["minutes"] += s["seconds"] / 60
    daily_rows = [{"date": v["date"], "owners": len(v["owners"]), "sessions": v["sessions"], "minutes": _round(v["minutes"], 0)}
                  for v in daily.values()]

    # --- by day / week / month: who came, how long, page visits and the top flows in each period
    by = by if by in ("day", "week", "month") else "day"
    per: dict[str, dict[str, Any]] = {}
    for s_ in all_sessions:
        k = period_key(local(s_["start"]), by)
        b = per.setdefault(k, {"period": k, "owners": set(), "sessions": 0, "secs": 0.0, "visits": Counter(), "actions": Counter(), "flows": Counter()})
        b["owners"].add(s_["user_id"])
        b["sessions"] += 1
        b["secs"] += s_["seconds"]
        prev = None
        for e in s_["events"]:
            if e["kind"] == "screen":
                b["visits"][screen_label(e)] += 1
            elif e["kind"] == "action":
                b["actions"][screen_label(e)] += 1
            else:
                continue
            label = screen_label(e)
            if prev and prev != label:
                b["flows"][f"{prev} → {label}"] += 1
            prev = label
    periods = [{"period": k, "owners": len(v["owners"]), "sessions": v["sessions"], "minutes": _round(v["secs"] / 60, 0),
                "page_visits": sum(v["visits"].values()), "actions": sum(v["actions"].values()),
                "visits": [{"name": n, "count": c} for n, c in v["visits"].most_common()],
                "top_actions": [{"name": n, "count": c} for n, c in v["actions"].most_common(8)],
                "top_flows": [{"path": n, "count": c} for n, c in v["flows"].most_common(8)]}
               for k, v in sorted(per.items())]

    # --- screens and actions
    by_screen: dict[str, dict[str, Any]] = defaultdict(lambda: {"opens": 0, "secs": 0, "owners": set(), "app": 0, "web": 0})
    for e in screens:
        b = by_screen[screen_label(e)]
        b["opens"] += 1
        b["secs"] += e["secs"]
        b["owners"].add(e["user_id"])
        b["web" if e.get("source") == "website" else "app"] += 1
    screen_rows = sorted(({"name": k, "opens": v["opens"], "minutes": _round(v["secs"] / 60), "owners": len(v["owners"]),
                           "avg_secs": round(v["secs"] / v["opens"]) if v["opens"] else 0, "app": v["app"], "web": v["web"]}
                          for k, v in by_screen.items()), key=lambda r: (-r["opens"], r["name"]))
    by_action: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "owners": set()})
    for e in actions:
        b = by_action[screen_label(e)]
        b["count"] += 1
        b["owners"].add(e["user_id"])
    action_rows = sorted(({"name": k, "count": v["count"], "owners": len(v["owners"])} for k, v in by_action.items()),
                         key=lambda r: (-r["count"], r["name"]))
    taps_by_screen: dict[str, list[list[float]]] = defaultdict(list)
    for e in events:
        d = e.get("detail") or {}
        if e["kind"] == "tap" and isinstance(d.get("x"), (int, float)) and isinstance(d.get("y"), (int, float)):
            taps_by_screen[SCREENS.get(e["screen"], e["screen"])].append([d["x"], d["y"]])
    tap_rows = sorted(({"name": k, "count": len(v), "spots": v[-300:]} for k, v in taps_by_screen.items()), key=lambda r: -r["count"])
    devices: dict[str, dict[str, Any]] = {}
    for e in events:
        if e["kind"] == "app_open" and e.get("detail"):
            devices[e["user_id"]] = e["detail"]
    seen_screens = {e["screen"] for e in screens}
    unused = {
        "screens": [SCREENS[s] for s in OWNER_SCREENS if s not in seen_screens],
        "actions": [a for a in OWNER_ACTIONS if a not in by_action],
        "rare_screens": [r["name"] for r in screen_rows if r["owners"] == 1 and len(owners) > 2],
    }

    # --- paths: next-page pairs, and the first three pages of a session
    pairs: Counter[str] = Counter()
    starts: Counter[str] = Counter()
    for s in all_sessions:
        steps: list[str] = []
        for e in s["events"]:
            if e["kind"] not in ("screen", "action"):
                continue
            label = screen_label(e)
            if not steps or steps[-1] != label:
                steps.append(label)
        for a, b in zip(steps, steps[1:]):
            pairs[f"{a} → {b}"] += 1
        if len(steps) >= 2:
            starts[" → ".join(steps[:3])] += 1
    paths = {"pairs": [{"path": k, "count": v} for k, v in pairs.most_common(15)],
             "starts": [{"path": k, "count": v} for k, v in starts.most_common(10)]}

    # --- first move each day (first page or action that isn't the landing page)
    firsts: Counter[str] = Counter()
    first_by_owner: dict[str, Counter[str]] = defaultdict(Counter)
    seen_day: set[tuple[str, str]] = set()
    for e in sorted((e for e in events if e["kind"] in ("screen", "action", "push_open")), key=lambda e: e["at"]):
        if e["kind"] == "screen" and e["screen"] in LANDING:
            continue
        key = (e["user_id"], local(e["at"]).date().isoformat())
        if key in seen_day:
            continue
        seen_day.add(key)
        label = screen_label(e)
        firsts[label] += 1
        first_by_owner[e["user_id"]][label] += 1
    first_moves = [{"name": k, "count": v} for k, v in firsts.most_common(10)]

    # --- when: weekday x hour heatmap, and what each weekday is used for
    heat = [[0] * 24 for _ in range(7)]
    by_day: dict[int, dict[str, Any]] = {d: {"events": 0, "owners": set(), "dates": set(), "screens": Counter(), "actions": Counter()} for d in range(7)}
    for e in events:
        if e["kind"] not in ("screen", "action"):
            continue
        t = local(e["at"])
        heat[t.weekday()][t.hour] += 1
        b = by_day[t.weekday()]
        b["events"] += 1
        b["owners"].add((e["user_id"], t.date()))
        b["dates"].add(t.date())
        b["screens" if e["kind"] == "screen" else "actions"][screen_label(e)] += 1
    weekdays = []
    for d in range(7):
        b = by_day[d]
        weekdays.append({"day": WEEKDAYS[d], "events": b["events"],
                         "avg_owners": _round(len(b["owners"]) / max(1, len(b["dates"]))),
                         "top_screens": [k for k, _ in b["screens"].most_common(3)],
                         "top_actions": [k for k, _ in b["actions"].most_common(3)]})
    hours = [sum(heat[d][h] for d in range(7)) for h in range(24)]

    # --- owners
    ai_by_owner: dict[str, dict[str, Any]] = defaultdict(lambda: {"questions": 0, "cost": 0.0})
    topics: Counter[str] = Counter()
    for a in ai_rows:
        if not a.get("user_id"):
            continue
        b = ai_by_owner[str(a["user_id"])]
        b["cost"] += float(a.get("cost_usd") or 0)
        if a.get("feature") == "chat":
            b["questions"] += 1
            topics[str(a.get("topic") or "other")] += 1
    owner_rows = []
    for user_id, o in owners.items():
        mine = [s for s in all_sessions if s["user_id"] == user_id]
        evs = [e for s in mine for e in s["events"]]
        last = max((e["at"] for e in evs), default=None)
        builds = [e.get("build") for e in sorted(evs, key=lambda e: e["at"]) if e.get("build")]
        app_secs = sum(s["seconds"] for s in mine if s["source"] != "website")
        web_secs = sum(s["seconds"] for s in mine if s["source"] == "website")
        owner_rows.append({
            "user_id": user_id, "name": who(user_id), "team": o.get("team"), "last_seen": local(last).isoformat() if last else None,
            "days_since": (now - last).days if last else None, "days_active": len({local(e["at"]).date() for e in evs}),
            "sessions": len(mine), "minutes": _round((app_secs + web_secs) / 60, 0),
            "app_share": round(100 * app_secs / (app_secs + web_secs)) if app_secs + web_secs else None,
            "build": builds[-1] if builds else None, "actions": sum(1 for e in evs if e["kind"] == "action"),
            "gm_questions": ai_by_owner[user_id]["questions"], "gm_cost": _round(ai_by_owner[user_id]["cost"], 2),
            "favorite_first_move": (first_by_owner[user_id].most_common(1) or [(None, 0)])[0][0],
            "taps": sum(1 for e in evs if e["kind"] == "tap"), "app_opens": sum(1 for e in evs if e["kind"] == "app_open"),
            "push_opens": sum(1 for e in evs if e["kind"] == "push_open"),
            "device": {k: (devices.get(user_id) or {}).get(k) for k in ("os", "idiom", "version", "build", "tz", "width", "height", "font_scale")}
                      if devices.get(user_id) else None,
        })
    owner_rows.sort(key=lambda r: (-(r["minutes"] or 0), r["name"]))
    quiet = [r["name"] for r in owner_rows if r["days_since"] is None or r["days_since"] >= 7]
    builds_in_use = Counter(r["build"] for r in owner_rows if r["build"])
    errors = Counter(screen_label(e) for e in events if e["kind"] == "error")

    # --- session timelines (newest first; one owner when asked)
    picked = [s for s in all_sessions if not owner or s["user_id"] == owner]
    timelines = []
    for s in reversed(picked[-25:]):
        timelines.append({
            "owner": who(s["user_id"]), "user_id": s["user_id"], "source": s["source"], "start": local(s["start"]).isoformat(),
            "minutes": _round(s["seconds"] / 60),
            "frames": [{"at": e["at"].isoformat(), "path": (e.get("detail") or {}).get("path")} for e in s["events"]
                       if e["kind"] == "frame" and (e.get("detail") or {}).get("path")][:300],
            "steps": [{"at": local(e["at"]).strftime("%-I:%M:%S %p"), "name": screen_label(e), "kind": e["kind"], "secs": e["secs"],
                       "t": e["at"].isoformat(), **({"detail": e["detail"]} if e.get("detail") and e["kind"] in ("tap", "swipe") else {})}
                      for e in [e for e in s["events"] if e["kind"] != "frame"][:150]],
        })

    return {
        "days": days, "tz": str(zone), "by": by, "periods": periods, "totals": totals, "daily": daily_rows, "screens": screen_rows, "actions": action_rows,
        "unused": unused, "paths": paths, "first_moves": first_moves, "heatmap": heat, "hours": hours, "weekdays": weekdays,
        "owners": owner_rows, "quiet": quiet, "builds": [{"build": k, "owners": v} for k, v in sorted(builds_in_use.items(), reverse=True)],
        "gm_topics": [{"topic": k, "count": v} for k, v in topics.most_common()], "errors": [{"name": k, "count": v} for k, v in errors.most_common(10)],
        "sessions": timelines, "taps": tap_rows,
        "platforms": dict(Counter((d.get("os") or "?").split(" ")[0] + (" " + str(d.get("idiom")) if d.get("idiom") else "") for d in devices.values())),
    }


def _all(query: Any, size: int = 1000) -> list[dict[str, Any]]:
    rows, start = [], 0
    while True:
        page = query.range(start, start + size - 1).execute().data or []
        rows += page
        if len(page) < size:
            return rows
        start += size


def owners_from_teams(teams: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """user_id -> name/team from commissioner.overview()['teams'] (each team's people)."""
    out: dict[str, dict[str, Any]] = {}
    for t in teams:
        for p in t.get("people") or []:
            if p.get("user_id"):
                out[str(p["user_id"])] = {"name": t.get("owner_name") or (p.get("email") or "").split("@")[0] or t.get("team_name"),
                                          "team": t.get("team_name")}
    return out


def load(client: Any, league_id: str, *, days: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Raw activity and GM-usage rows for the league (needs the commissioner read policies in owner-activity.sql)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = _all(client.table("app_screen_views").select("user_id,screen,seconds,viewed_at,source,kind,build,detail")
                    .eq("league_id", league_id).gte("viewed_at", since).order("id"))
    except Exception:  # before the SQL adds the kind column
        rows = _all(client.table("app_screen_views").select("user_id,screen,seconds,viewed_at,source,build")
                    .eq("league_id", league_id).gte("viewed_at", since).order("id"))
    try:
        ai = _all(client.table("league_ai_usage").select("user_id,feature,topic,cost_usd,created_at")
                  .eq("league_id", league_id).gte("created_at", since).order("id"))
    except Exception:
        ai = []
    return rows, ai
