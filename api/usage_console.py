"""Data for the Owner activity console (api/console/console.html): the page the league owner opens in the app
(a web view) and on the website (Settings > League tools > Owner activity). Only accounts in
app_activity_viewers get any of it (api/main.py checks, and the database's RLS does too).

The console works its charts out in the browser from one compact feed, so every filter, breakdown, funnel and
report is instant. A session opens on demand with its steps and replay pictures. The Activity analyst
(api/usage_ai.py) uses the same events and the same report runner, so a report it builds is exactly what the
console draws.

Feed shape (lists keep it small):
  owners   [name, ...]               an owner's number is its place in this list
  labels   [label, ...]              page and action names
  kinds    [kind, ...]
  events   [[owner, unix, kind, label, secs, web 0/1, session, build], ...]
  sessions [[owner, unix start, secs, [label, ...] path, replay pictures, key], ...]
"""
from __future__ import annotations

import gzip
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from api import usage_stats

KINDS = ["screen", "action", "tap", "swipe", "push_open", "app_open", "app_close", "error", "load", "rage", "search", "outbound", "refresh", "feedback", "fail"]
METRICS = ("Active owners", "Sessions", "Page views", "Actions", "Taps", "Minutes", "Alert opens", "Feedback sent", "Check-in rating",
           "Dead taps", "Rage taps", "Searches", "Failed loads")
GROUPS = ("Day", "Week", "Month", "Weekday", "Hour", "Page", "Action", "Owner", "Platform", "Build")
SPLITS = ("None", "Platform", "Owner", "Page", "Action", "Build", "Weekday")
CHARTS = ("Bars", "Line", "Table")
REPORT_KEYS = ("name", "metric", "group", "split", "page", "kind", "owner", "chart", "days", "videos", "note")


def session_key(user_id: str, start: datetime) -> str:
    return f"{user_id}|{int(start.timestamp())}"


class Data:
    """Every event of the range in one flat list, with local time fields, plus the sessions they belong to."""

    def __init__(self, rows: list[dict[str, Any]], *, owners: dict[str, dict[str, Any]], tz: str | None):
        try:
            self.zone = ZoneInfo(tz or usage_stats.DEFAULT_TZ)
        except Exception:
            self.zone = ZoneInfo(usage_stats.DEFAULT_TZ)
        self.tz = str(self.zone)
        self.rows = rows
        self.sessions = usage_stats.sessions(rows)
        ids = list(owners)
        names = {uid: (o.get("name") or o.get("team") or "Owner") for uid, o in owners.items()}
        for s in self.sessions:  # former members still show, under a placeholder name
            if s["user_id"] not in names:
                ids.append(s["user_id"])
                names[s["user_id"]] = "Former member"
        self.ids = ids
        self.names = names
        self.owners = owners
        self.events: list[dict[str, Any]] = []
        self.frames: Counter[int] = Counter()
        for si, s in enumerate(self.sessions):
            for e in s["events"]:
                if e["kind"] == "frame":
                    self.frames[si] += 1
                    continue
                if e["kind"] not in KINDS:
                    continue
                t = e["at"].astimezone(self.zone)
                on_screen = e["kind"] in ("screen", "action")
                self.events.append({
                    "uid": s["user_id"], "owner": names[s["user_id"]], "kind": e["kind"],
                    "label": usage_stats.screen_label(e) if on_screen else usage_stats.page_label(e["screen"]),
                    "name": usage_stats.screen_label(e), "secs": int(e["secs"] or 0), "web": s["source"] == "website", "sess": si,
                    "build": e.get("build") or "", "unix": int(e["at"].timestamp()), "time": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "date": t.date().isoformat(), "week": usage_stats.period_key(t, "week"), "month": t.strftime("%Y-%m"),
                    "weekday": usage_stats.WEEKDAYS[t.weekday()], "hour": t.hour, "detail": e.get("detail"),
                })

    def path(self, si: int) -> list[str]:
        out: list[str] = []
        for e in self.sessions[si]["events"]:
            if e["kind"] in ("screen", "action"):
                label = usage_stats.screen_label(e)
                if not out or out[-1] != label:
                    out.append(label)
        return out

    def session_row(self, si: int) -> dict[str, Any]:
        s = self.sessions[si]
        return {"key": session_key(s["user_id"], s["start"]), "owner": self.names[s["user_id"]], "source": s["source"],
                "start": s["start"].astimezone(self.zone).strftime("%a %b %-d %-I:%M %p"), "minutes": round(s["seconds"] / 60, 1),
                "pictures": self.frames[si], "path": self.path(si)[:12]}


def feed(rows: list[dict[str, Any]], *, owners: dict[str, dict[str, Any]], ai_rows: list[dict[str, Any]], days: int, tz: str | None) -> dict[str, Any]:
    data = Data(rows, owners=owners, tz=tz)
    index = {uid: i for i, uid in enumerate(data.ids)}
    labels: list[str] = []
    label_ix: dict[str, int] = {}

    def li(label: str) -> int:
        if label not in label_ix:
            label_ix[label] = len(labels)
            labels.append(label)
        return label_ix[label]

    events = []
    for e in data.events:
        row = [index[e["uid"]], e["unix"], KINDS.index(e["kind"]), li(e["label"]), e["secs"], 1 if e["web"] else 0, e["sess"], e["build"]]
        extra = compact_detail(e["kind"], e.get("detail"))
        if extra is not None:
            row.append(extra)
        events.append(row)
    sessions = [[index[s["user_id"]], int(s["start"].timestamp()), round(s["seconds"]), [li(x) for x in data.path(si)], data.frames[si],
                 session_key(s["user_id"], s["start"])] for si, s in enumerate(data.sessions)]
    summary = usage_stats.build(rows, owners=owners, ai_rows=ai_rows, days=days, tz=tz)
    owner_rows = {o["user_id"]: o for o in summary["owners"]}
    return {
        "now": int(datetime.now(timezone.utc).timestamp()), "days": days, "tz": data.tz,
        "owners": [data.names[uid] for uid in data.ids],
        "owner_info": [{k: (owner_rows.get(uid) or {}).get(k) for k in ("gm_questions", "gm_cost", "build", "device", "favorite_first_move")}
                       for uid in data.ids],
        "labels": labels, "kinds": KINDS, "events": events, "sessions": sessions,
        "taps": [{"name": t["name"], "count": t["count"], "spots": t["spots"][-200:]} for t in summary["taps"]],
        "gm_topics": summary["gm_topics"], "unused": summary["unused"], "errors": summary["errors"],
        "gm": [[index[str(a["user_id"])], _unix(a.get("created_at")), str(a.get("topic") or "other")] for a in ai_rows
               if a.get("feature") == "chat" and str(a.get("user_id")) in index and _unix(a.get("created_at"))],
        "feedback": [[index[str(a["user_id"])], _unix(a.get("created_at")), str(a.get("kind") or ""), a.get("rating"), str(a.get("text") or ""),
                      usage_stats.page_label(str(a.get("screen") or "/"))] for a in ai_rows
                     if a.get("feature") == "feedback" and str(a.get("user_id")) in index],
        "alerts_sent": [[index[str(a["user_id"])], _unix(a.get("created_at")), str(a.get("topic") or "alert")] for a in ai_rows
                        if a.get("feature") == "alert_sent" and str(a.get("user_id")) in index and _unix(a.get("created_at"))],
    }


def _unix(value: Any) -> int:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def _num(v: Any, default: float = -1) -> float:
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return default


WENT = {"none": 0, "press": 1, "page": 2, "input": 3}


def compact_detail(kind: str, d: dict[str, Any] | None) -> list[Any] | None:
    """The few detail fields the console draws, as a short list (9th item of an event row):
    tap [x, y, page y or -1, went: 0 nothing / 1 button / 2 new page / 3 keyboard / -1 unknown (older build)]
    swipe [x, y, to x, to y, page y]   rage [x, y, page y, taps]   load [ms, data asked for]
    screen [% of page reached, % that fits on one screen, seconds per tenth "a,b,...", stops per tenth, page path]
    search [words, results]   outbound [where, link]   push_open [alert kind, page]"""
    if not d:
        return None
    if kind == "tap":
        return [_num(d.get("x")), _num(d.get("y")), _num(d.get("py")), WENT.get(str(d.get("went")), -1)]
    if kind == "swipe":
        return [_num(d.get("x")), _num(d.get("y")), _num(d.get("to_x")), _num(d.get("to_y")), _num(d.get("py"))]
    if kind == "rage":
        return [_num(d.get("x")), _num(d.get("y")), _num(d.get("py")), int(_num(d.get("n"), 3))]
    if kind == "load":
        return [int(_num(d.get("ms"), 0)), str(d.get("api") or "")]
    if kind == "screen":
        if d.get("depth") is None and not d.get("path"):
            return None
        return [int(_num(d.get("depth"), -1)), int(_num(d.get("fold"), -1)), str(d.get("attn") or ""), str(d.get("stops") or ""), str(d.get("path") or "")]
    if kind == "search":
        return [str(d.get("q") or "")[:60], int(_num(d.get("found"), -1))]
    if kind == "outbound":
        return [str(d.get("to") or ""), str(d.get("url") or "")]
    if kind == "feedback":
        return [str(d.get("kind") or ""), d.get("rating") if isinstance(d.get("rating"), int) else -1, str(d.get("text") or "")]
    if kind == "fail":
        return [str(d.get("api") or ""), 1 if d.get("offline") else 0, int(_num(d.get("status"), 0))]
    if kind == "push_open":
        return [str(d.get("alert") or ""), str(d.get("to") or "")]
    return None


def gz_json(payload: Any, accept_encoding: str) -> tuple[bytes, dict[str, str]]:
    """JSON body, gzipped when the browser takes it (the feed shrinks about 8x)."""
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    headers = {"Cache-Control": "no-store"}
    if "gzip" in (accept_encoding or "").lower() and len(body) > 2048:
        body = gzip.compress(body, 6)
        headers["Content-Encoding"] = "gzip"
    return body, headers


def session_detail(rows: list[dict[str, Any]], *, owners: dict[str, dict[str, Any]], key: str, tz: str | None) -> dict[str, Any] | None:
    """One session's steps (with tap spots) and replay picture paths, by its key ('user_id|unix start')."""
    user_id, _, start = key.partition("|")
    try:
        start_ts = int(start)
    except ValueError:
        return None
    try:
        zone = ZoneInfo(tz or usage_stats.DEFAULT_TZ)
    except Exception:
        zone = ZoneInfo(usage_stats.DEFAULT_TZ)
    for s in usage_stats.sessions(rows):
        if s["user_id"] == user_id and int(s["start"].timestamp()) == start_ts:
            o = owners.get(user_id) or {}
            return usage_stats.timeline(s, o.get("name") or o.get("team") or "Former member", zone)
    return None


def backdrops(rows: list[dict[str, Any]], page: str, *, keep: int = 4) -> dict[str, Any]:
    """Real pictures of one page for its heat map, newest first: 'screen' are replay pictures taken just after
    the page opened (so it's at the top, as most taps see it); 'page' is the newest whole-page picture (top to
    bottom, from build 113) with how many screens tall it is."""
    screen: list[tuple[float, str]] = []
    full: tuple[float, str, float] | None = None
    for s in usage_stats.sessions(rows):
        opened: datetime | None = None
        for e in s["events"]:
            kind = e["kind"]
            if kind == "screen" and usage_stats.page_label(e["screen"]) == page:
                opened = e["at"]
                continue
            if kind != "frame":
                continue
            d = e.get("detail") or {}
            path = d.get("path")
            if not path or usage_stats.page_label(e["screen"]) != page:
                continue
            at = e["at"].timestamp()
            if d.get("full"):
                if full is None or at > full[0]:
                    full = (at, path, float(d.get("screens") or 0))
            elif opened is not None and 0 <= (e["at"] - opened).total_seconds() <= 4:
                screen.append((at, path))
                opened = None
    screen.sort(reverse=True)
    newest = list(dict.fromkeys(p for _, p in screen))[:keep]
    return {"screen": newest, "page": {"path": full[1], "screens": full[2]} if full else None}


def sign_frames(token: str, paths: list[str], *, expires: int = 3600, post: Callable[..., Any] | None = None) -> dict[str, str]:
    """Short-lived links to replay pictures, signed AS the signed-in viewer, so the bucket's own rule
    (only app_activity_viewers may read) decides. Returns {path: url}."""
    if not paths:
        return {}
    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    if post is None:
        import httpx

        post = httpx.post
    out: dict[str, str] = {}
    for i in range(0, len(paths), 100):
        resp = post(f"{base}/storage/v1/object/sign/replays", json={"expiresIn": expires, "paths": paths[i:i + 100]}, timeout=20,
                    headers={"Authorization": f"Bearer {token}", "apikey": os.getenv("SUPABASE_ANON_KEY", "")})
        if resp.status_code >= 400:
            continue
        for x in resp.json() or []:
            url = x.get("signedURL") or x.get("signedUrl")
            if url and x.get("path"):
                out[x["path"]] = url if url.startswith("http") else f"{base}/storage/v1{url}"
    return out


# --- reports: one runner shared by the analyst (server) and mirrored by the console (browser) -----------------

def clean_report(spec: dict[str, Any]) -> dict[str, Any]:
    """A report keeps only known fields, with known values."""
    out: dict[str, Any] = {}
    name = str(spec.get("name") or "").strip()[:80]
    out["name"] = name or "Untitled report"
    out["metric"] = spec.get("metric") if spec.get("metric") in METRICS else "Page views"
    out["group"] = spec.get("group") if spec.get("group") in GROUPS else "Day"
    out["split"] = spec.get("split") if spec.get("split") in SPLITS else "None"
    out["chart"] = spec.get("chart") if spec.get("chart") in CHARTS else "Bars"
    out["kind"] = spec.get("kind") if spec.get("kind") in KINDS else ""
    for k in ("page", "owner", "note"):
        out[k] = str(spec.get(k) or "").strip()[:200]
    try:
        out["days"] = max(0, min(int(spec.get("days") or 0), 365))  # 0 = follow the console's range
    except (TypeError, ValueError):
        out["days"] = 0
    out["videos"] = bool(spec.get("videos"))
    return out


def _avg_rating(es: list[dict[str, Any]]) -> float:
    r = [int((e.get("detail") or {}).get("rating")) for e in es if e["kind"] == "feedback" and isinstance((e.get("detail") or {}).get("rating"), int)]
    return round(sum(r) / len(r), 1) if r else 0


_METRIC_FN: dict[str, Callable[[list[dict[str, Any]]], float]] = {
    "Active owners": lambda es: len({e["uid"] for e in es}),
    "Sessions": lambda es: len({e["sess"] for e in es}),
    "Page views": lambda es: sum(1 for e in es if e["kind"] == "screen"),
    "Actions": lambda es: sum(1 for e in es if e["kind"] == "action"),
    "Taps": lambda es: sum(1 for e in es if e["kind"] == "tap"),
    "Minutes": lambda es: round(sum(e["secs"] for e in es if e["kind"] == "screen") / 60),
    "Alert opens": lambda es: sum(1 for e in es if e["kind"] == "push_open"),
    "Feedback sent": lambda es: sum(1 for e in es if e["kind"] == "feedback" and (e.get("detail") or {}).get("kind") in ("pulse", "button")),
    "Check-in rating": lambda es: _avg_rating(es),
    "Dead taps": lambda es: sum(1 for e in es if e["kind"] == "tap" and (e.get("detail") or {}).get("went") == "none"),
    "Rage taps": lambda es: sum(1 for e in es if e["kind"] == "rage"),
    "Searches": lambda es: sum(1 for e in es if e["kind"] == "search"),
    "Failed loads": lambda es: sum(1 for e in es if e["kind"] == "fail"),
}
_GROUP_FN: dict[str, Callable[[dict[str, Any]], Any]] = {
    "Day": lambda e: e["date"], "Week": lambda e: e["week"], "Month": lambda e: e["month"], "Weekday": lambda e: e["weekday"],
    "Hour": lambda e: f"{e['hour']:02d}:00", "Page": lambda e: e["label"] if e["kind"] == "screen" else None,
    "Action": lambda e: e["label"] if e["kind"] == "action" else None, "Owner": lambda e: e["owner"],
    "Platform": lambda e: "Website" if e["web"] else "App", "Build": lambda e: e["build"] or "(web)", "None": lambda e: None,
}


def report_events(data: Data, spec: dict[str, Any], *, days: int, now: datetime | None = None) -> list[dict[str, Any]]:
    now_ts = int((now or datetime.now(timezone.utc)).timestamp())
    lo = now_ts - (spec.get("days") or days) * 86400
    es = [e for e in data.events if e["unix"] > lo]
    if spec.get("page"):
        es = [e for e in es if e["label"] == spec["page"]]
    if spec.get("kind"):
        es = [e for e in es if e["kind"] == spec["kind"]]
    if spec.get("owner"):
        es = [e for e in es if str(spec["owner"]).lower() in e["owner"].lower()]
    return es


def run_report(data: Data, spec: dict[str, Any], *, days: int, now: datetime | None = None) -> dict[str, Any]:
    """{keys, series: [{name, data}], sessions: [matching sessions, newest first]} for a clean report spec."""
    spec = clean_report(spec)
    es = report_events(data, spec, days=days, now=now)
    metric = _METRIC_FN[spec["metric"]]
    g = _GROUP_FN[spec["group"]]
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for e in es:
        k = g(e)
        if k is not None:
            groups[k].append(e)
    keys = list(groups)
    if spec["group"] == "Weekday":
        keys = [d for d in usage_stats.WEEKDAYS if d in groups]
    elif spec["group"] in ("Page", "Action", "Owner", "Platform"):
        keys.sort(key=lambda k: -metric(groups[k]))
    else:
        keys.sort()
    keys = keys[:60 if spec["group"] == "Day" else 20]
    if spec["split"] != "None":
        sg = _GROUP_FN[spec["split"]]
        split_groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for e in es:
            v = sg(e)
            if v is not None:
                split_groups[v].append(e)
        top = sorted(split_groups, key=lambda v: -metric(split_groups[v]))[:5]
        series = [{"name": v, "data": [metric([e for e in groups[k] if sg(e) == v]) for k in keys]} for v in top]
    else:
        series = [{"name": spec["metric"], "data": [metric(groups[k]) for k in keys]}]
    sess = sorted({e["sess"] for e in es}, reverse=True)
    return {"spec": spec, "keys": keys, "series": series, "sessions": [data.session_row(si) for si in sess[:20]], "session_count": len(sess)}


def find_sessions(data: Data, *, owner: str = "", page: str = "", action: str = "", weekday: str = "", since: str = "", until: str = "",
                  source: str = "", min_minutes: float = 0, has_pictures: bool = False, limit: int = 10) -> dict[str, Any]:
    """Sessions that match (newest first); each one opens as a replay in the console."""
    by_sess: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in data.events:
        by_sess[e["sess"]].append(e)
    out = []
    for si in sorted(by_sess, reverse=True):
        es = by_sess[si]
        s = data.sessions[si]
        first = es[0]
        if owner and owner.lower() not in first["owner"].lower():
            continue
        if source and (source == "website") != first["web"]:
            continue
        if weekday and first["weekday"] != weekday:
            continue
        if since and first["date"] < since[:10]:
            continue
        if until and first["date"] > until[:10]:
            continue
        if page and not any(e["kind"] == "screen" and page.lower() in e["label"].lower() for e in es):
            continue
        if action and not any(e["kind"] == "action" and action.lower() in e["label"].lower() for e in es):
            continue
        if min_minutes and s["seconds"] < min_minutes * 60:
            continue
        if has_pictures and not data.frames[si]:
            continue
        out.append(data.session_row(si))
    return {"sessions": out[:max(1, min(limit, 25))], "matched": len(out)}
