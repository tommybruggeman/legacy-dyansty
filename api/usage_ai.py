"""The Activity analyst: the commissioner asks anything about how owners use the app and the website
("who skipped setting lineups this week?", "what do people do first on Thursdays?", "which pages does
nobody scroll?") and Claude answers from our own activity rows, using the tools below to count, filter
and read sessions. Shared by the app and the website.

Cost: Haiku by default (about a cent a question); "dig deeper" uses Sonnet (a few cents). Each call is
logged in league_ai_usage as feature 'usage_analyst', so it shows in the AI cost report.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from api import usage_stats

FAST_MODEL = "claude-haiku-5-5"
DEEP_MODEL = "claude-sonnet-5-5"
MAX_ROUNDS = 8
MAX_ROWS = 300

SYSTEM = """You are the usage analyst for a private 12-team fantasy football league's app (iPhone app + website).
The commissioner asks about how owners use the product. Answer from the data your tools return; never guess numbers.

What is recorded, per signed-in owner (names are real; everyone is signed in):
- screen: a page view with seconds spent (app times every screen; website pages get time from the gap to the next page)
- action: something they did (set a lineup, sent a trade, asked the GM, placed a bid...)
- tap / swipe: every touch in the app, with the spot on the screen (x, y as 0-1 of width/height)
- app_open / app_close: the app coming to the front (with OS, build, time zone, screen size) and leaving
- push_open: opened the app from an alert; error: a crash or screen error; frame: a replay picture
- GM usage: questions to the Front Office AI, with topic and cost
A session is one owner's activity on one platform with no gap over 30 minutes. Times are in the commissioner's time zone.

How to answer:
- Start with the direct answer in one or two sentences, then the few numbers that back it up. Keep it short;
  use a small list or table only when it helps. Name owners when it matters.
- Say how much data there is when it's thin (a few days, a few owners) so the commissioner doesn't over-read it.
- If the data can't answer (we don't record it), say what's missing and what we could start recording."""

FIELDS = ("owner", "screen", "kind", "source", "weekday", "hour", "date", "week", "month", "build")

TOOLS = [
    {"name": "overview", "description": "Headline numbers for the whole range: active owners, sessions, top screens and actions, paths, first moves each day, weekday/hour patterns, unused screens and actions, per-owner totals, builds, GM topics, errors.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "count_events", "description": "Count events grouped by one or more fields, after optional filters. Use for any 'how many / which most / by day or hour' question. Returns events, distinct owners and total minutes per group.",
     "input_schema": {"type": "object", "properties": {
         "group_by": {"type": "array", "items": {"type": "string", "enum": list(FIELDS)}},
         "owner": {"type": "string", "description": "Owner name (or part of it)"},
         "kind": {"type": "string", "enum": ["screen", "action", "tap", "swipe", "push_open", "app_open", "app_close", "error"]},
         "screen": {"type": "string", "description": "Part of a screen or action name, e.g. 'My Team' or 'lineup'"},
         "weekday": {"type": "string", "enum": list(usage_stats.WEEKDAYS)},
         "since": {"type": "string", "description": "ISO date, inclusive"}, "until": {"type": "string", "description": "ISO date, inclusive"},
         "source": {"type": "string", "enum": ["app", "website"]}},
         "required": ["group_by"]}},
    {"name": "list_events", "description": "Raw events in time order (newest last) after the same filters as count_events. Up to 300.",
     "input_schema": {"type": "object", "properties": {
         "owner": {"type": "string"}, "kind": {"type": "string"}, "screen": {"type": "string"}, "weekday": {"type": "string"},
         "since": {"type": "string"}, "until": {"type": "string"}, "source": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "sessions", "description": "Session timelines (newest first), optionally for one owner: each step with time, name, kind and seconds.",
     "input_schema": {"type": "object", "properties": {"owner": {"type": "string"}, "limit": {"type": "integer"}}}},
]


class Analyst:
    def __init__(self, rows: list[dict[str, Any]], *, owners: dict[str, dict[str, Any]], ai_rows: list[dict[str, Any]], days: int, tz: str | None):
        self.owners = owners
        self.ai_rows = ai_rows
        self.days = days
        try:
            self.zone = ZoneInfo(tz or usage_stats.DEFAULT_TZ)
        except Exception:
            self.zone = ZoneInfo(usage_stats.DEFAULT_TZ)
        self.tz = str(self.zone)
        self.rows = rows
        self.events: list[dict[str, Any]] = []
        for s in usage_stats.sessions(rows):
            for e in s["events"]:
                if e["kind"] == "frame":
                    continue
                t = e["at"].astimezone(self.zone)
                o = owners.get(e["user_id"]) or {}
                self.events.append({"owner": o.get("name") or o.get("team") or "Former member", "screen": usage_stats.screen_label(e),
                                    "kind": e["kind"], "source": e.get("source") or "app", "weekday": usage_stats.WEEKDAYS[t.weekday()],
                                    "hour": t.hour, "date": t.date().isoformat(), "week": usage_stats.period_key(t, "week"), "month": t.strftime("%Y-%m"), "time": t.strftime("%Y-%m-%d %H:%M:%S"),
                                    "build": e.get("build"), "secs": e["secs"], "detail": e.get("detail")})

    def _filter(self, a: dict[str, Any]) -> list[dict[str, Any]]:
        out = self.events
        if a.get("owner"):
            out = [e for e in out if str(a["owner"]).lower() in e["owner"].lower()]
        for key in ("kind", "weekday", "source"):
            if a.get(key):
                out = [e for e in out if e[key] == a[key]]
        if a.get("screen"):
            out = [e for e in out if str(a["screen"]).lower() in e["screen"].lower()]
        if a.get("since"):
            out = [e for e in out if e["date"] >= str(a["since"])[:10]]
        if a.get("until"):
            out = [e for e in out if e["date"] <= str(a["until"])[:10]]
        return out

    def run_tool(self, name: str, a: dict[str, Any]) -> Any:
        if name == "overview":
            out = usage_stats.build(self.rows, owners=self.owners, ai_rows=self.ai_rows, days=self.days, tz=self.tz)
            out.pop("sessions", None)
            out["taps"] = [{"name": t["name"], "count": t["count"]} for t in out.get("taps", [])]
            out["daily"] = [d for d in out["daily"] if d["sessions"]]
            return out
        if name == "count_events":
            group = [g for g in (a.get("group_by") or []) if g in FIELDS] or ["screen"]
            counts: dict[tuple, dict[str, Any]] = {}
            for e in self._filter(a):
                key = tuple(e[g] for g in group)
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
            return {"events": [{k: e[k] for k in ("time", "owner", "kind", "screen", "secs", "source", "detail") if e.get(k) is not None}
                               for e in evs[-limit:]], "matched": len(evs)}
        if name == "sessions":
            owner_id = None
            if a.get("owner"):
                owner_id = next((uid for uid, o in self.owners.items() if str(a["owner"]).lower() in str(o.get("name") or "").lower()), None)
            out = usage_stats.build(self.rows, owners=self.owners, days=self.days, tz=self.tz, owner=owner_id)["sessions"]
            limit = max(1, min(int(a.get("limit") or 8), 25))
            for s in out:
                s.pop("frames", None)
                s.pop("user_id", None)
                for st in s["steps"]:
                    st.pop("t", None)
            return out[:limit]
        return {"error": f"unknown tool {name}"}


def ask(question: str, *, rows: list[dict[str, Any]], owners: dict[str, dict[str, Any]], ai_rows: list[dict[str, Any]], days: int,
        tz: str | None, deep: bool = False, history: list[dict[str, str]] | None = None, sdk: Any = None) -> dict[str, Any]:
    """One answer. Returns {"answer", "model", "usage", "tools", "latency_ms"}."""
    from league_ai import usage

    if sdk is None:
        import anthropic

        from league_ai.config import api_key

        sdk = anthropic.Anthropic(api_key=api_key(), timeout=90.0, max_retries=2)
    analyst = Analyst(rows, owners=owners, ai_rows=ai_rows, days=days, tz=tz)
    model = DEEP_MODEL if deep else FAST_MODEL
    today = datetime.now(timezone.utc).astimezone(analyst.zone)
    system = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": f"Today is {today:%A %Y-%m-%d %H:%M} ({analyst.tz}). Data covers the last {days} days. "
                                       f"League members: {', '.join(sorted(str(o.get('name') or o.get('team')) for o in owners.values()))}."}]
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
    return {"answer": answer or "I couldn't work that out from the data. Try asking it another way.", "model": model,
            "usage": dict(totals), "tools": used, "latency_ms": int((time.monotonic() - started) * 1000)}
