"""The Activity analyst: the league owner asks anything about how owners use the app and the website
("who skipped setting lineups this week?", "what do people do first on Thursdays?", "build me a report of
trade-builder visits by owner", "show me sessions where someone gave up on a trade") and Claude answers from
our own activity rows. It sees everything the console shows: every event, page, action, tap, flow, funnel,
retention, owner profile and session, through the tools below, and it can hand back a report (the console
draws it and can save it) and sessions (the console plays them as replays). Shared by the app and the website.

Cost: Haiku by default (about a cent a question); "dig deeper" uses Sonnet (a few cents). Each call is
logged in league_ai_usage as feature 'usage_analyst', so it shows in the AI cost report.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from api import usage_console, usage_stats

FAST_MODEL = "claude-haiku-5-5"
DEEP_MODEL = "claude-sonnet-5-5"
MAX_ROUNDS = 10
MAX_ROWS = 300

SYSTEM = """You are the usage analyst for a private 12-team fantasy football league's product (iPhone app + website).
The league owner (the only person who can see this) asks about how the league's owners use it. You have the whole
record through your tools. Answer from what they return; never guess or invent numbers.

What is recorded, per signed-in owner (names are real; everyone is signed in):
- screen: a page view with seconds spent (the app times every screen; website pages get time from the gap to the next page)
- action: something they did (set a lineup, sent a trade, asked the GM, placed a bid...)
- tap / swipe: every touch in the app, with the spot on the screen (x, y as 0-1 of width/height)
- app_open / app_close: the app coming to the front (with OS, build, time zone, screen size) and leaving
- push_open: opened the app from an alert; error: a crash or screen error
- replay pictures: small screenshots during app sessions, so a session can be watched like a video
- GM usage: questions to the Front Office AI, with topic and cost
A session is one owner's activity on one platform with no gap over 30 minutes. Times are in the viewer's time zone.

Tools: overview (headline numbers), count_events (any breakdown), list_events (raw rows), page_table, flows,
funnel, retention, owner_profile, find_sessions, run_report (try a chart and read its numbers), and two that
put something on the viewer's screen: build_report (a chart they can save) and show_sessions (replays to watch).

How to answer:
- Lead with the direct answer in one or two sentences, then the few numbers that back it up. Keep it short;
  use a small list or table only when it helps. Name owners when it matters.
- When a chart would make the answer clearer, or the viewer asks for a report, call build_report with the
  report that shows it (check it with run_report first if unsure). When the viewer wants to see what someone
  did, or examples would help, call show_sessions. Don't describe the chart or list again in words; one line
  pointing to it is enough.
- Say how much data there is when it's thin (a few days, a few owners) so nobody over-reads it.
- If the data can't answer (we don't record it), say what's missing and what we could start recording."""

FIELDS = ("owner", "screen", "kind", "source", "weekday", "hour", "date", "week", "month", "build")
_FILTERS = {
    "owner": {"type": "string", "description": "Owner name (or part of it)"},
    "kind": {"type": "string", "enum": usage_console.KINDS},
    "screen": {"type": "string", "description": "Part of a page or action name, e.g. 'My Team' or 'lineup'"},
    "weekday": {"type": "string", "enum": list(usage_stats.WEEKDAYS)},
    "since": {"type": "string", "description": "ISO date, inclusive"}, "until": {"type": "string", "description": "ISO date, inclusive"},
    "source": {"type": "string", "enum": ["app", "website"]},
}
_REPORT = {
    "name": {"type": "string"},
    "metric": {"type": "string", "enum": list(usage_console.METRICS)},
    "group": {"type": "string", "enum": list(usage_console.GROUPS)},
    "split": {"type": "string", "enum": list(usage_console.SPLITS)},
    "page": {"type": "string", "description": "Exact page or action name to keep (from overview/page_table), or empty"},
    "kind": {"type": "string", "enum": ["", *usage_console.KINDS]},
    "owner": {"type": "string", "description": "Owner name to keep, or empty for everyone"},
    "chart": {"type": "string", "enum": list(usage_console.CHARTS)},
    "days": {"type": "integer", "description": "Range in days; 0 follows the viewer's range"},
    "videos": {"type": "boolean", "description": "List the matching sessions under the chart as replays"},
    "note": {"type": "string", "description": "One line on what the report shows"},
}

TOOLS = [
    {"name": "overview", "description": "Headline numbers for the whole range: active owners, sessions, top screens and actions, paths, first moves each day, weekday/hour patterns, unused screens and actions, per-owner totals, builds, GM topics, errors.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "count_events", "description": "Count events grouped by one or more fields, after optional filters. Use for any 'how many / which most / by day or hour' question. Returns events, distinct owners and total minutes per group.",
     "input_schema": {"type": "object", "properties": {"group_by": {"type": "array", "items": {"type": "string", "enum": list(FIELDS)}}, **_FILTERS},
                      "required": ["group_by"]}},
    {"name": "list_events", "description": "Raw events in time order (newest last) after the same filters as count_events, with tap spots. Up to 300.",
     "input_schema": {"type": "object", "properties": {**_FILTERS, "limit": {"type": "integer"}}}},
    {"name": "page_table", "description": "Every page: views, owners, average seconds, minutes, taps, how often it's the first stop after Home, and exit % (last page before leaving).",
     "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "source": {"type": "string", "enum": ["app", "website"]}}}},
    {"name": "flows", "description": "How owners move: the most common session paths, page-to-next-page steps, and what comes right after a given page.",
     "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "after": {"type": "string", "description": "Page or action name: what do people do next"}}}},
    {"name": "funnel", "description": "Sessions reaching each step in order (e.g. ['League','Another team','Trade builder','Sent a trade offer']), with drop-off per step. Steps are page or action names.",
     "input_schema": {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "string"}}, "owner": {"type": "string"}}, "required": ["steps"]}},
    {"name": "retention", "description": "Weekly active owners, came-back-next-week %, stickiness (daily ÷ weekly active), days active per owner, and who has gone quiet.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "owner_profile", "description": "Everything about one owner: totals, device, build, top pages and actions, usual first move, weekday/hour habits, GM questions, recent sessions.",
     "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}}, "required": ["owner"]}},
    {"name": "find_sessions", "description": "Sessions that match (newest first): by owner, a page visited, an action done, weekday, dates, platform, length, or having replay pictures.",
     "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "page": {"type": "string"}, "action": {"type": "string"}, "weekday": {"type": "string"},
                                                       "since": {"type": "string"}, "until": {"type": "string"}, "source": {"type": "string", "enum": ["app", "website"]},
                                                       "min_minutes": {"type": "number"}, "has_pictures": {"type": "boolean"}, "limit": {"type": "integer"}}}},
    {"name": "session_steps", "description": "Every step of one session (from find_sessions' key): pages, actions, taps with spots, seconds.",
     "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "run_report", "description": "Run a report (metric grouped by a field, optional split and filters) and get its numbers back without showing it. Use to check before build_report.",
     "input_schema": {"type": "object", "properties": _REPORT, "required": ["metric", "group"]}},
    {"name": "build_report", "description": "Show a report to the viewer: the console draws it under your answer and they can save it. Set videos=true to list the matching sessions as replays. Call once.",
     "input_schema": {"type": "object", "properties": _REPORT, "required": ["name", "metric", "group"]}},
    {"name": "show_sessions", "description": "Show sessions to the viewer as replays they can watch, by key (from find_sessions or run_report). Up to 10.",
     "input_schema": {"type": "object", "properties": {"keys": {"type": "array", "items": {"type": "string"}}, "title": {"type": "string"}}, "required": ["keys"]}},
]


class Analyst:
    def __init__(self, rows: list[dict[str, Any]], *, owners: dict[str, dict[str, Any]], ai_rows: list[dict[str, Any]], days: int, tz: str | None,
                 now: datetime | None = None):
        self.data = usage_console.Data(rows, owners=owners, tz=tz)
        self.zone = self.data.zone
        self.tz = self.data.tz
        self.owners = owners
        self.ai_rows = ai_rows
        self.days = days
        self.rows = rows
        self.now = now or datetime.now(timezone.utc)
        self.events = self.data.events
        self.report: dict[str, Any] | None = None
        self.shown: list[dict[str, Any]] = []
        self.shown_title = ""

    def _owner_events(self, owner: str | None, source: str | None = None) -> list[dict[str, Any]]:
        es = self.events
        if owner:
            es = [e for e in es if owner.lower() in e["owner"].lower()]
        if source:
            es = [e for e in es if e["web"] == (source == "website")]
        return es

    def _filter(self, a: dict[str, Any]) -> list[dict[str, Any]]:
        out = self._owner_events(a.get("owner"), a.get("source"))
        for key in ("kind", "weekday"):
            if a.get(key):
                out = [e for e in out if e[key] == a[key]]
        if a.get("screen"):
            out = [e for e in out if str(a["screen"]).lower() in e["name"].lower()]
        if a.get("since"):
            out = [e for e in out if e["date"] >= str(a["since"])[:10]]
        if a.get("until"):
            out = [e for e in out if e["date"] <= str(a["until"])[:10]]
        return out

    def _paths(self, owner: str | None) -> list[list[str]]:
        keep = {e["sess"] for e in self._owner_events(owner)}
        return [self.data.path(si) for si in range(len(self.data.sessions)) if si in keep]

    def context(self) -> str:
        """Up-front context so simple questions need no tool call: who, what pages and actions exist, the totals."""
        t = usage_stats.build(self.rows, owners=self.owners, ai_rows=self.ai_rows, days=self.days, tz=self.tz, now=self.now)["totals"]
        pages = Counter(e["label"] for e in self.events if e["kind"] == "screen")
        acts = Counter(e["label"] for e in self.events if e["kind"] == "action")
        first = min((e["date"] for e in self.events), default=None)
        return (f"Totals: {json.dumps(t)}\nData from {first or 'nothing yet'} to today.\n"
                f"Pages seen (views): {json.dumps(pages.most_common(40))}\nActions seen: {json.dumps(acts.most_common(40))}\n"
                f"Report metrics: {', '.join(usage_console.METRICS)}. Groups: {', '.join(usage_console.GROUPS)}.")

    def run_tool(self, name: str, a: dict[str, Any]) -> Any:
        if name == "overview":
            out = usage_stats.build(self.rows, owners=self.owners, ai_rows=self.ai_rows, days=self.days, tz=self.tz, now=self.now)
            out.pop("sessions", None)
            out["taps"] = [{"name": t["name"], "count": t["count"]} for t in out.get("taps", [])]
            out["daily"] = [d for d in out["daily"] if d["sessions"]]
            for o in out["owners"]:
                o.pop("user_id", None)
            return out
        if name == "count_events":
            group = [g for g in (a.get("group_by") or []) if g in FIELDS] or ["screen"]
            counts: dict[tuple, dict[str, Any]] = {}
            for e in self._filter(a):
                key = tuple(e["name"] if g == "screen" else ("website" if e["web"] else "app") if g == "source" else e[g] for g in group)
                b = counts.setdefault(key, {"events": 0, "owners": set(), "secs": 0})
                b["events"] += 1
                b["owners"].add(e["owner"])
                b["secs"] += e["secs"]
            rows = [{**dict(zip(group, k)), "events": v["events"], "owners": len(v["owners"]), "minutes": round(v["secs"] / 60, 1)}
                    for k, v in counts.items()]
            rows.sort(key=lambda r: -r["events"])
            return {"groups": rows[:MAX_ROWS], "total_groups": len(rows)}
        if name == "list_events":
            evs = self._filter(a)
            limit = max(1, min(int(a.get("limit") or 100), MAX_ROWS))
            return {"events": [{"time": e["time"], "owner": e["owner"], "kind": e["kind"], "screen": e["name"], "secs": e["secs"] or None,
                                "source": "website" if e["web"] else "app", **({"detail": e["detail"]} if e.get("detail") else {})}
                               for e in evs[-limit:]], "matched": len(evs)}
        if name == "page_table":
            es = self._owner_events(a.get("owner"), a.get("source"))
            views = [e for e in es if e["kind"] == "screen"]
            by_sess: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for e in views:
                by_sess[e["sess"]].append(e)
            entry, exit_ = Counter(), Counter()
            for v in by_sess.values():
                after_home = next((e for e in v if e["label"] != "Home"), v[0])
                entry[after_home["label"]] += 1
                exit_[v[-1]["label"]] += 1
            taps = Counter(e["label"] for e in es if e["kind"] == "tap")
            rows = []
            for page, vs in _group(views, "label").items():
                secs = sum(e["secs"] for e in vs)
                rows.append({"page": page, "views": len(vs), "owners": len({e["uid"] for e in vs}), "avg_secs": round(secs / len(vs)),
                             "minutes": round(secs / 60), "taps": taps[page], "first_stop": entry[page], "exit_pct": round(100 * exit_[page] / len(vs))})
            rows.sort(key=lambda r: -r["views"])
            return {"pages": rows}
        if name == "flows":
            paths = self._paths(a.get("owner"))
            pairs = Counter((p[i], p[i + 1]) for p in paths for i in range(len(p) - 1))
            out: dict[str, Any] = {"sessions": len(paths),
                                   "top_paths": [{"path": " → ".join(k), "count": v} for k, v in Counter(tuple(p[:4]) for p in paths if p).most_common(15)],
                                   "top_steps": [{"step": f"{x} → {y}", "count": v} for (x, y), v in pairs.most_common(20)]}
            if a.get("after"):
                want = str(a["after"]).lower()
                nxt = Counter(y for (x, y), v in pairs.items() for _ in range(v) if want in x.lower())
                ended = sum(1 for p in paths if p and want in p[-1].lower())
                out["after"] = {"page": a["after"], "next": [{"name": k, "count": v} for k, v in nxt.most_common(12)], "left_the_app": ended}
            return out
        if name == "funnel":
            steps = [str(s) for s in (a.get("steps") or []) if s][:8]
            paths = self._paths(a.get("owner"))
            counts = []
            for i in range(len(steps)):
                n = 0
                for p in paths:
                    j = 0
                    for x in p:
                        if j < len(steps) and x.lower() == steps[j].lower():
                            j += 1
                    n += j > i
                counts.append(n)
            return {"steps": [{"step": s, "sessions": c, "pct_of_start": round(100 * c / counts[0]) if counts and counts[0] else 0,
                               "drop_from_previous_pct": round(100 * (counts[i - 1] - c) / counts[i - 1]) if i and counts[i - 1] else None}
                              for i, (s, c) in enumerate(zip(steps, counts))], "sessions_checked": len(paths)}
        if name == "retention":
            weeks = sorted({e["week"] for e in self.events})
            active = {w: {e["owner"] for e in self.events if e["week"] == w} for w in weeks}
            daily = defaultdict(set)
            for e in self.events:
                daily[(e["week"], e["date"])].add(e["owner"])
            rows = []
            for i, w in enumerate(weeks):
                days = [v for (wk, _), v in daily.items() if wk == w]
                avg = sum(len(v) for v in days) / max(1, len(days))
                prev = active[weeks[i - 1]] if i else set()
                rows.append({"week": w, "active_owners": len(active[w]), "stickiness_pct": round(100 * avg / max(1, len(active[w]))),
                             "came_back_from_last_week_pct": round(100 * len(prev & active[w]) / len(prev)) if prev else None})
            last = {o: max(e["unix"] for e in self.events if e["owner"] == o) for o in {e["owner"] for e in self.events}}
            now = int(self.now.timestamp())
            return {"weeks": rows, "note": "The newest week is still in progress.",
                    "days_active": {o: len({e["date"] for e in self.events if e["owner"] == o}) for o in self.data.names.values()},
                    "quiet_7_days": [o for o in self.data.names.values() if o not in last or now - last[o] >= 7 * 86400]}
        if name == "owner_profile":
            want = str(a.get("owner") or "").lower()
            uid = next((u for u, n in self.data.names.items() if want and want in n.lower()), None)
            if not uid:
                return {"error": "No owner by that name.", "owners": sorted(self.data.names.values())}
            summary = usage_stats.build(self.rows, owners=self.owners, ai_rows=self.ai_rows, days=self.days, tz=self.tz, owner=uid, now=self.now)
            row = next((o for o in summary["owners"] if o["user_id"] == uid), {})
            row.pop("user_id", None)
            es = [e for e in self.events if e["uid"] == uid]
            return {"owner": self.data.names[uid], "totals": row,
                    "top_pages": Counter(e["label"] for e in es if e["kind"] == "screen").most_common(10),
                    "top_actions": Counter(e["label"] for e in es if e["kind"] == "action").most_common(10),
                    "by_weekday": Counter(e["weekday"] for e in es if e["kind"] in ("screen", "action")),
                    "by_hour": Counter(e["hour"] for e in es if e["kind"] in ("screen", "action")),
                    "gm_topics": Counter(str(r.get("topic") or "other") for r in self.ai_rows if str(r.get("user_id")) == uid and r.get("feature") == "chat").most_common(8),
                    "recent_sessions": usage_console.find_sessions(self.data, owner=self.data.names[uid], limit=8)["sessions"]}
        if name == "find_sessions":
            return usage_console.find_sessions(self.data, **{k: a[k] for k in ("owner", "page", "action", "weekday", "since", "until", "source",
                                                                               "min_minutes", "has_pictures") if a.get(k) not in (None, "")},
                                               limit=int(a.get("limit") or 10))
        if name == "session_steps":
            s = usage_console.session_detail(self.rows, owners=self.owners, key=str(a.get("key") or ""), tz=self.tz)
            if not s:
                return {"error": "No session with that key."}
            return {"owner": s["owner"], "source": s["source"], "start": s["start"], "minutes": s["minutes"], "pictures": len(s["frames"]),
                    "steps": [{k: v for k, v in st.items() if k != "t"} for st in s["steps"]]}
        if name in ("run_report", "build_report"):
            out = usage_console.run_report(self.data, a, days=self.days, now=self.now)
            if name == "build_report":
                self.report = out["spec"]
                return {"shown": True, "keys": out["keys"], "series": out["series"], "matching_sessions": out["session_count"]}
            return {**out, "sessions": out["sessions"][:10]}
        if name == "show_sessions":
            keys = [str(k) for k in (a.get("keys") or [])][:10]
            index = {usage_console.session_key(s["user_id"], s["start"]): si for si, s in enumerate(self.data.sessions)}
            self.shown = [self.data.session_row(index[k]) for k in keys if k in index]
            self.shown_title = str(a.get("title") or "")[:80]
            return {"shown": len(self.shown)}
        return {"error": f"unknown tool {name}"}


def _group(events: list[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        out[e[key]].append(e)
    return out


def ask(question: str, *, rows: list[dict[str, Any]], owners: dict[str, dict[str, Any]], ai_rows: list[dict[str, Any]], days: int,
        tz: str | None, deep: bool = False, history: list[dict[str, str]] | None = None, sdk: Any = None) -> dict[str, Any]:
    """One answer. Returns {"answer", "report", "sessions", "sessions_title", "model", "usage", "tools", "latency_ms"}."""
    from league_ai import usage

    if sdk is None:
        import anthropic

        from league_ai.config import api_key

        sdk = anthropic.Anthropic(api_key=api_key(), timeout=90.0, max_retries=2)
    analyst = Analyst(rows, owners=owners, ai_rows=ai_rows, days=days, tz=tz)
    model = DEEP_MODEL if deep else FAST_MODEL
    today = datetime.now(timezone.utc).astimezone(analyst.zone)
    system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": f"Today is {today:%A %Y-%m-%d %H:%M} ({analyst.tz}). The viewer's range is the last {days} days. "
                                       f"League members: {', '.join(sorted(analyst.data.names[u] for u in analyst.data.ids))}.\n{analyst.context()}"}]
    messages: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in (history or [])[-6:]
                                      if m.get("role") in ("user", "assistant") and m.get("content")]
    messages.append({"role": "user", "content": question})
    totals: Counter[str] = Counter()
    used: list[str] = []
    started = time.monotonic()
    answer = ""
    for round_no in range(MAX_ROUNDS):
        kwargs: dict[str, Any] = {"model": model, "max_tokens": 1500, "system": system, "messages": messages, "tools": TOOLS}
        if round_no == MAX_ROUNDS - 1:
            kwargs["tool_choice"] = {"type": "none"}
        resp = sdk.messages.create(**kwargs)
        for k, v in usage.response_usage(resp).items():
            totals[k] += v
        content = list(resp.content)
        calls = [b for b in content if getattr(b, "type", None) == "tool_use"]
        answer = "".join(getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text").strip()
        if not calls:
            break
        messages.append({"role": "assistant", "content": [b.model_dump() if hasattr(b, "model_dump") else b for b in content]})
        results = []
        for c in calls:
            used.append(c.name)
            try:
                out = analyst.run_tool(c.name, dict(c.input or {}))
            except Exception as exc:
                out = {"error": type(exc).__name__}
            text = json.dumps(out, default=str)
            results.append({"type": "tool_result", "tool_use_id": c.id, "content": text[:60_000]})
        messages.append({"role": "user", "content": results})
    return {"answer": answer or "I couldn't work that out from the data. Try asking it another way.", "report": analyst.report,
            "sessions": analyst.shown, "sessions_title": analyst.shown_title, "model": model,
            "usage": dict(totals), "tools": used, "latency_ms": int((time.monotonic() - started) * 1000)}
