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

KINDS = ["screen", "action", "tap", "swipe", "push_open", "app_open", "app_close", "error"]
METRICS = ("Active owners", "Sessions", "Page views", "Actions", "Taps", "Minutes", "Alert opens")
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
                    "label": usage_stats.screen_label(e) if on_screen else usage_stats.SCREENS.get(e["screen"], e["screen"]),
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

    events = [[index[e["uid"]], e["unix"], KINDS.index(e["kind"]), li(e["label"]), e["secs"], 1 if e["web"] else 0, e["sess"], e["build"]]
              for e in data.events]
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
    }


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


_METRIC_FN: dict[str, Callable[[list[dict[str, Any]]], float]] = {
    "Active owners": lambda es: len({e["uid"] for e in es}),
    "Sessions": lambda es: len({e["sess"] for e in es}),
    "Page views": lambda es: sum(1 for e in es if e["kind"] == "screen"),
    "Actions": lambda es: sum(1 for e in es if e["kind"] == "action"),
    "Taps": lambda es: sum(1 for e in es if e["kind"] == "tap"),
    "Minutes": lambda es: round(sum(e["secs"] for e in es if e["kind"] == "screen") / 60),
    "Alert opens": lambda es: sum(1 for e in es if e["kind"] == "push_open"),
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
