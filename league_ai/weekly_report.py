"""Weekly League AI report: what it cost, who used it, which models did what, the calls it made and graded,
and the work it did on its own. Saved to league_ai_reports (Settings -> League AI) and emailed.

    python -m league_ai.weekly_report                 # last 7 days, every league with activity
    python -m league_ai.weekly_report --no-email      # build and save only
    python -m league_ai.weekly_report --print         # print the plain-text version

Costs are list-price figures computed from the token counts on every call (league_ai_usage);
Anthropic's invoice is the source of truth for billing.

Privacy: by default the report shows each owner's activity (topics, counts, cost, calls) but
not the text of other owners' questions, because the AI promises owners their chats stay
private. Set REPORT_DETAIL=full to include question text for everyone.
"""
from __future__ import annotations

import argparse
import html
import os
import smtplib
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Iterable, Mapping

from pipeline.nfl_data.common import service_client  # also puts the repo root on sys.path

from league_ai.usage import BATCH_DISCOUNT

FEATURE_LABEL = {
    "chat": "Chat answers",
    "brief": "Weekly briefs",
    "grading": "Grading misses (full diagnostic)",
    "grading_review": "Reviewing correct calls",
    "playbook": "Updating the playbook",
}


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _rows(q: Any) -> list[dict[str, Any]]:
    try:
        return q.execute().data or []
    except Exception as exc:
        print(f"[report] query failed: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        return []


def _in_window(value: Any, start: datetime, end: datetime) -> bool:
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return start <= t < end


def _money(v: float) -> str:
    return f"${v:,.2f}" if v >= 0.995 or v == 0 else f"${v:.3f}"


def _tally(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0, "cost": 0.0, "input": 0, "output": 0, "cache_read": 0, "web_searches": 0})
    for r in rows:
        k = str(r.get(key) or "none")
        o = out[k]
        o["calls"] += 1
        o["cost"] += _num(r.get("cost_usd"))
        o["input"] += int(_num(r.get("input_tokens")) + _num(r.get("cache_write_tokens")))
        o["output"] += int(_num(r.get("output_tokens")))
        o["cache_read"] += int(_num(r.get("cache_read_tokens")))
        o["web_searches"] += int(_num(r.get("web_searches")))
    return {k: {**v, "cost": round(v["cost"], 4)} for k, v in sorted(out.items(), key=lambda x: -x[1]["cost"])}


def build_report(client: Any, league_id: str, start: datetime, end: datetime, *, detail: str = "summary") -> dict[str, Any]:
    owners = _owner_names(client, league_id)
    name = lambda uid: owners.get(str(uid), "Unknown owner") if uid else "League AI (on its own)"  # noqa: E731

    usage = [r for r in _rows(client.table("league_ai_usage").select("*").eq("league_id", league_id).order("created_at", desc=True).limit(20000))
             if _in_window(r.get("created_at"), start, end)]
    season_start = datetime(start.year if start.month >= 8 else start.year - 1, 8, 1, tzinfo=timezone.utc)
    season_usage = [r for r in _rows(client.table("league_ai_usage").select("cost_usd, created_at").eq("league_id", league_id).limit(100000))
                    if _in_window(r.get("created_at"), season_start, end)]
    total = round(sum(_num(r.get("cost_usd")) for r in usage), 4)
    batch_saved = round(sum(_num(r.get("cost_usd")) for r in usage if r.get("batch")) * (1 / BATCH_DISCOUNT - 1), 4)

    # chats
    convs = [c for c in _rows(client.table("league_ai_conversations").select("id, user_id, title, messages, created_at, updated_at").eq("league_id", league_id)
                               .order("updated_at", desc=True).limit(500)) if _in_window(c.get("updated_at"), start, end)]
    chat_usage = [r for r in usage if r.get("feature") == "chat"]
    by_conv_cost: dict[str, float] = defaultdict(float)
    by_conv_topics: dict[str, Counter] = defaultdict(Counter)
    by_conv_models: dict[str, Counter] = defaultdict(Counter)
    for r in chat_usage:
        cid = str(r.get("conversation_id") or "")
        by_conv_cost[cid] += _num(r.get("cost_usd"))
        by_conv_topics[cid][r.get("topic") or "other"] += 1
        by_conv_models[cid][f"{r.get('model')} · {r.get('effort') or 'default'} effort"] += 1
    preds_all = _rows(client.table("league_ai_predictions").select("*").eq("league_id", league_id).order("created_at", desc=True).limit(5000))
    logged = [p for p in preds_all if _in_window(p.get("created_at"), start, end)]
    graded = [p for p in preds_all if p.get("status") == "graded" and _in_window(p.get("graded_at"), start, end)]
    calls_by_conv = Counter(str(p.get("conversation_id") or "") for p in logged)
    chats = []
    for c in convs:
        cid = str(c["id"])
        questions = [m for m in (c.get("messages") or []) if m.get("role") == "user"]
        chats.append({
            "owner": name(c.get("user_id")), "started": str(c.get("created_at") or "")[:10], "last_active": str(c.get("updated_at") or "")[:16].replace("T", " "),
            "questions_total": len(questions), "answers_this_week": sum(by_conv_topics[cid].values()),
            "topics": dict(by_conv_topics[cid]), "models": dict(by_conv_models[cid]), "calls_logged": calls_by_conv.get(cid, 0),
            "cost": round(by_conv_cost[cid], 4),
            "title": c.get("title") if detail == "full" else None,
            "questions": [str(m.get("content") or "")[:300] for m in questions][-12:] if detail == "full" else None,
        })
    per_owner: dict[str, dict[str, Any]] = defaultdict(lambda: {"answers": 0, "cost": 0.0, "calls_logged": 0, "topics": Counter()})
    for r in chat_usage:
        o = per_owner[name(r.get("user_id"))]
        o["answers"] += 1
        o["cost"] += _num(r.get("cost_usd"))
        o["topics"][r.get("topic") or "other"] += 1
    for p in logged:
        per_owner[name(p.get("user_id"))]["calls_logged"] += 1

    # calls
    def call_line(p: Mapping[str, Any]) -> dict[str, Any]:
        picks = ", ".join(x.get("name", "?") for x in p.get("pick") or [])
        overs = ", ".join(x.get("name", "?") for x in p.get("over") or [])
        hidden = p.get("kind") == "trade" and detail != "full"  # trade interest is sensitive; show it only in full mode
        text = ("trade evaluation (players hidden)" if hidden else
                f"{p.get('verdict')} trade: get {picks} for {overs}" if p.get("kind") == "trade" else
                f"{'start' if p.get('kind') == 'start_sit' else 'add'} {picks} over {overs}")
        return {"owner": name(p.get("user_id")), "kind": p.get("kind"), "week": p.get("week"), "call": text,
                "confidence": p.get("confidence"), "factors": p.get("factors"), "status": p.get("status"),
                "result": None if p.get("correct") is None else ("right" if p["correct"] else "wrong"),
                "points": None if p.get("pick_points") is None else f"{_num(p.get('pick_points')):.1f} vs {_num(p.get('over_points')):.1f}",
                "cause": p.get("cause"), "process_error": p.get("process_error"), "diagnosis": None if hidden else p.get("diagnosis"), "lesson": p.get("lesson")}

    right = sum(1 for p in graded if p.get("correct"))
    lessons = _rows(client.table("league_ai_lessons").select("*").eq("league_id", league_id).limit(500))
    added = [l for l in lessons if _in_window(l.get("created_at"), start, end)]
    retired = [l for l in lessons if not l.get("active") and _in_window(l.get("updated_at"), start, end)]
    active = [l for l in lessons if l.get("active")]
    card = _rows(client.table("league_ai_scorecards").select("stats, season").eq("league_id", league_id).order("season", desc=True).limit(1))
    briefs = [r for r in usage if r.get("feature") == "brief"]
    grading_runs = [r for r in usage if r.get("feature") in ("grading", "grading_review", "playbook")]
    errors = [r for r in usage if not r.get("ok")]
    tools = Counter(t for r in chat_usage for t in (r.get("tool_calls") or []))
    latency = sorted(int(_num(r.get("latency_ms"))) for r in chat_usage if r.get("ok"))
    cache_read = sum(int(_num(r.get("cache_read_tokens"))) for r in chat_usage)
    cache_total = cache_read + sum(int(_num(r.get("input_tokens")) + _num(r.get("cache_write_tokens"))) for r in chat_usage)

    return {
        "league_id": league_id, "window": {"start": start.date().isoformat(), "end": (end - timedelta(seconds=1)).date().isoformat()},
        "detail": detail,
        "cost": {
            "total": total, "season_to_date": round(sum(_num(r.get("cost_usd")) for r in season_usage), 4), "batch_savings": batch_saved,
            "by_feature": {FEATURE_LABEL.get(k, k): v for k, v in _tally(usage, "feature").items()},
            "by_model": _tally(usage, "model"),
            "chat_by_effort": _tally(chat_usage, "effort"),
            "chat_by_topic": _tally(chat_usage, "topic"),
            "avg_per_answer": round(sum(_num(r.get("cost_usd")) for r in chat_usage) / len(chat_usage), 4) if chat_usage else 0,
            "web_searches": sum(int(_num(r.get("web_searches"))) for r in usage),
        },
        "usage": {
            "answers": len(chat_usage), "conversations": len(convs), "active_owners": len({r.get("user_id") for r in chat_usage}),
            "per_owner": {k: {**v, "cost": round(v["cost"], 4), "topics": dict(v["topics"])} for k, v in sorted(per_owner.items(), key=lambda x: -x[1]["answers"])},
            "tools_used": dict(tools.most_common()),
            "cache_hit_rate": round(cache_read / cache_total, 3) if cache_total else None,
            "median_answer_seconds": round(latency[len(latency) // 2] / 1000, 1) if latency else None,
            "errors": [{"when": str(r.get("created_at"))[:16], "feature": r.get("feature"), "model": r.get("model")} for r in errors],
        },
        "chats": chats,
        "calls": {
            "logged": len(logged), "logged_by_kind": dict(Counter(p.get("kind") for p in logged)),
            "graded": len(graded), "right": right, "wrong": len(graded) - right,
            "causes_of_misses": dict(Counter(p.get("cause") for p in graded if p.get("correct") is False and p.get("cause"))),
            "process_errors": sum(1 for p in graded if p.get("process_error")),
            "graded_list": [call_line(p) for p in graded],
            "logged_list": [call_line(p) for p in logged],
            "still_open": sum(1 for p in preds_all if p.get("status") == "open"),
        },
        "autonomous": {
            "briefs_written": len(briefs), "brief_cost": round(sum(_num(r.get("cost_usd")) for r in briefs), 4),
            "grading_model_calls": len(grading_runs), "grading_cost": round(sum(_num(r.get("cost_usd")) for r in grading_runs), 4),
            "lessons_added": [l["lesson"] for l in added], "lessons_retired": [l["lesson"] for l in retired],
            "playbook_now": [l["lesson"] for l in sorted(active, key=lambda l: -int(_num(l.get("evidence_count"))))],
            "scorecard": (card[0]["stats"] if card else None),
        },
    }


def _owner_names(client: Any, league_id: str) -> dict[str, str]:
    teams = {str(t["id"]): t.get("owner_name") or t.get("team_name") for t in _rows(client.table("league_teams").select("id, owner_name, team_name").eq("league_id", league_id))}
    out = {}
    for m in _rows(client.table("league_memberships").select("user_id, league_team_id").eq("league_id", league_id)):
        out[str(m["user_id"])] = teams.get(str(m.get("league_team_id")), "Member without a team")
    return out


# ---- rendering ---------------------------------------------------------------------------

def _table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "<p style='color:#777'>None this week.</p>"
    th = "".join(f"<th style='text-align:left;padding:4px 10px;border-bottom:1px solid #ccc'>{html.escape(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td style='padding:4px 10px;border-bottom:1px solid #eee;vertical-align:top'>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table style='border-collapse:collapse;font-size:14px'>{th and '<tr>' + th + '</tr>'}{trs}</table>"


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def render_html(rep: Mapping[str, Any], league_name: str = "the league") -> str:
    c, u, k, a = rep["cost"], rep["usage"], rep["calls"], rep["autonomous"]
    tok = lambda v: f"{int(v['input']):,} in / {int(v['output']):,} out" + (f" / {int(v['cache_read']):,} cached" if v.get("cache_read") else "")  # noqa: E731
    parts = [
        f"<h2 style='margin-bottom:0'>League AI weekly report · {_e(league_name)}</h2>",
        f"<p style='color:#666;margin-top:4px'>{_e(rep['window']['start'])} to {_e(rep['window']['end'])}. Costs are list prices computed from token counts; your Anthropic invoice is the source of truth.</p>",
        f"<h3>Cost: {_money(c['total'])} this week · {_money(c['season_to_date'])} season to date</h3>",
        f"<p>{u['answers']} answers across {u['conversations']} conversations from {u['active_owners']} owners · average {_money(c['avg_per_answer'])} per answer · "
        f"batching saved {_money(c['batch_savings'])} · {c['web_searches']} web searches · cache hit rate {('%d%%' % round(100 * u['cache_hit_rate'])) if u['cache_hit_rate'] is not None else 'n/a'} · "
        f"median answer time {u['median_answer_seconds'] or 'n/a'}s</p>",
        "<h4>By part of the system</h4>", _table(["Part", "Model calls", "Tokens", "Cost"], [[_e(f), int(v["calls"]), tok(v), _money(v["cost"])] for f, v in c["by_feature"].items()]),
        "<h4>By model</h4>", _table(["Model", "Calls", "Tokens", "Web searches", "Cost"], [[_e(m), int(v["calls"]), tok(v), int(v["web_searches"]), _money(v["cost"])] for m, v in c["by_model"].items()]),
        "<h4>Chat answers by thinking effort</h4>", _table(["Effort", "Answers", "Cost"], [[_e(e), int(v["calls"]), _money(v["cost"])] for e, v in c["chat_by_effort"].items()]),
        "<h4>Chat answers by topic</h4>", _table(["Topic", "Answers", "Cost"], [[_e(t), int(v["calls"]), _money(v["cost"])] for t, v in c["chat_by_topic"].items()]),
        "<h3>Who used it</h3>", _table(["Owner", "Answers", "Calls logged", "Topics", "Cost"],
                                       [[_e(o), v["answers"], v["calls_logged"], _e(", ".join(f"{t} {n}" for t, n in v["topics"].items())), _money(v["cost"])] for o, v in u["per_owner"].items()]),
        "<h3>Conversations</h3>",
        _table(["Owner", "Last active", "Answers this week", "Topics", "Model · effort", "Calls", "Cost"] + (["Questions"] if rep["detail"] == "full" else []),
               [[_e(ch["owner"]), _e(ch["last_active"]), ch["answers_this_week"], _e(", ".join(f"{t} {n}" for t, n in ch["topics"].items())),
                 _e("; ".join(f"{m} ×{n}" for m, n in ch["models"].items())), ch["calls_logged"], _money(ch["cost"])]
                + (["<br>".join(_e(q) for q in ch["questions"] or [])] if rep["detail"] == "full" else []) for ch in rep["chats"]]),
        "<h4>Tools the AI used</h4><p>" + (_e(", ".join(f"{t} ×{n}" for t, n in u["tools_used"].items())) or "None") + "</p>",
        f"<h3>Calls: {k['logged']} logged this week, {k['graded']} graded ({k['right']} right, {k['wrong']} wrong), {k['still_open']} waiting on games</h3>",
        f"<p>Logged by kind: {_e(', '.join(f'{x} {n}' for x, n in k['logged_by_kind'].items()) or 'none')}. "
        f"Causes of misses: {_e(', '.join(f'{x} {n}' for x, n in k['causes_of_misses'].items()) or 'none')}. Bad-reasoning misses: {k['process_errors']}.</p>",
        _table(["Owner", "Week", "Call", "Result", "Points", "Why"],
               [[_e(g["owner"]), g["week"], _e(g["call"]), "✅" if g["result"] == "right" else "❌", _e(g["points"]), _e(g["diagnosis"] or g["cause"] or "")] for g in k["graded_list"]]),
        "<h4>Calls logged this week</h4>",
        _table(["Owner", "Kind", "Week", "Call", "Confidence", "Main factors", "Status"],
               [[_e(g["owner"]), _e(g["kind"]), g["week"], _e(g["call"]), "" if g["confidence"] is None else f"{round(100 * float(g['confidence']))}%",
                 _e(", ".join(f for f, w in (g["factors"] or {}).items() if w == "major")), _e(g["status"])] for g in k["logged_list"]]),
        "<h3>What it did on its own</h3>",
        f"<p>Wrote {a['briefs_written']} weekly briefs ({_money(a['brief_cost'])}). Made {a['grading_model_calls']} grading and playbook model calls ({_money(a['grading_cost'])}).</p>",
        "<h4>Lessons added this week</h4>" + ("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in a["lessons_added"]) + "</ul>" if a["lessons_added"] else "<p style='color:#777'>None.</p>"),
        "<h4>Lessons retired this week</h4>" + ("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in a["lessons_retired"]) + "</ul>" if a["lessons_retired"] else "<p style='color:#777'>None.</p>"),
        "<h4>Current playbook</h4>" + ("<ol>" + "".join(f"<li>{_e(x)}</li>" for x in a["playbook_now"]) + "</ol>" if a["playbook_now"] else "<p style='color:#777'>Empty so far.</p>"),
    ]
    if a.get("scorecard"):
        sc = a["scorecard"]
        parts.append("<h4>Season scorecard</h4><p>" + "<br>".join(_e(f"{label}: " + ", ".join(f"{x} {v}" for x, v in (sc.get(key) or {}).items()))
                                                             for key, label in (("by_kind", "By kind"), ("by_factor", "By main factor"), ("by_cause", "Causes of misses"), ("calibration", "Calibration"))
                                                             if sc.get(key)) + "</p>")
    if u["errors"]:
        parts.append("<h3>Errors</h3>" + _table(["When", "Part", "Model"], [[_e(x["when"]), _e(x["feature"]), _e(x["model"])] for x in u["errors"]]))
    if rep["detail"] != "full":
        parts.append("<p style='color:#777;font-size:12px'>Other owners' question text and trade details are hidden because the AI promises owners their chats are private. Set REPORT_DETAIL=full to include them.</p>")
    return "<div style='font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:900px'>" + "".join(parts) + "</div>"


def send_email(subject: str, body_html: str) -> bool:
    host, to = os.environ.get("SMTP_HOST", "").strip(), os.environ.get("REPORT_TO", "").strip()
    user, password = os.environ.get("SMTP_USER", "").strip(), os.environ.get("SMTP_PASSWORD", "").strip()
    if not (host and to and user and password):
        print("[report] email skipped: set SMTP_HOST, SMTP_USER, SMTP_PASSWORD and REPORT_TO to send it", flush=True)
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_FROM", user), to
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=60) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.sendmail(msg["From"], [x.strip() for x in to.split(",")], msg.as_string())
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--print", action="store_true")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args(argv)
    client = service_client()
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    detail = "full" if os.environ.get("REPORT_DETAIL", "").strip().lower() == "full" else "summary"
    leagues = _rows(client.table("leagues").select("id, name"))
    sent = 0
    for lg in leagues:
        rep = build_report(client, str(lg["id"]), start, end, detail=detail)
        if not (rep["usage"]["answers"] or rep["calls"]["logged"] or rep["calls"]["graded"] or rep["autonomous"]["briefs_written"]):
            continue
        body = render_html(rep, lg.get("name") or "the league")
        client.table("league_ai_reports").upsert({"league_id": lg["id"], "week_start": rep["window"]["start"], "week_end": rep["window"]["end"],
                                                  "report": rep, "html": body}, on_conflict="league_id,week_start").execute()
        print(f"[report] {lg.get('name')}: {_money(rep['cost']['total'])}, {rep['usage']['answers']} answers, {rep['calls']['logged']} calls logged", flush=True)
        if args.print:
            print(rep)
        if not args.no_email:
            sent += int(send_email(f"League AI weekly report · {lg.get('name')} · {_money(rep['cost']['total'])}", body))
    print(f"[report] done; {sent} emailed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
