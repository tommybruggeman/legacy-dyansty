"""Daily learning job: grade League AI's calls once their games are final, diagnose them, update the playbook.

    python -m league_ai.learning_job                # grade everything that's due
    python -m league_ai.learning_job --dry-run      # grade and diagnose, print, write nothing
    python -m league_ai.learning_job --no-search    # skip web search in diagnostics
    python -m league_ai.learning_job --no-batch     # answer immediately at full price (debugging)

Runs with the service role (GitHub Actions, after the nightly NFL data sync), so it can
grade every owner's calls; owners still only ever read their own.

Cost controls: the model work goes through Anthropic's Message Batches API (half price;
results usually within the hour). Misses get the full diagnostic on the main model with
web search; correct calls get a short "right for the right reason?" review on Haiku.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from pipeline.nfl_data.common import service_client  # also puts the repo root on sys.path

import requests

from league_ai.config import api_key, load_config
from league_ai.learning import (
    consolidate_request, diagnose_request, diagnostic_facts, grade, grade_weeks, is_due, league_points, parse_diagnosis, parse_playbook, scorecard,
)
from league_ai.settings_store import fetch_sleeper_scoring, load_settings
from league_ai.usage import record as record_usage, response_usage, usage_row

SLEEPER_STATS = "https://api.sleeper.app/v1/stats/nfl/regular/{season}/{week}"
REVIEW_MODEL = "claude-haiku-4-5"   # correct calls: short review
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


@dataclass
class Req:
    id: str
    system: str
    prompt: str
    model: str
    web_search: bool = False
    feature: str = "grading"
    league_id: str | None = None
    prediction_id: str | None = None


@dataclass
class Res:
    text: str
    usage: dict[str, int] = field(default_factory=dict)
    batch: bool = False


Runner = Callable[[list[Req]], dict[str, Res]]


def completed_week(client: Any, season: int) -> int:
    """Highest regular-season week whose games are all final (0 if none)."""
    rows = client.table("nfl_games").select("week, home_score").eq("season", season).eq("game_type", "REG").limit(400).execute().data or []
    weeks: dict[int, list[bool]] = defaultdict(list)
    for r in rows:
        weeks[int(r["week"])].append(r.get("home_score") is not None)
    done = 0
    for w in sorted(weeks):
        if all(weeks[w]):
            done = w
        else:
            break
    return done


class WeeklyStats:
    def __init__(self, get_json: Callable[[str], Any] | None = None):
        self.get_json = get_json or (lambda url: requests.get(url, timeout=60).json())
        self._cache: dict[tuple[int, int], dict[str, Any]] = {}

    def week(self, season: int, week: int) -> dict[str, Any]:
        key = (season, week)
        if key not in self._cache:
            self._cache[key] = self.get_json(SLEEPER_STATS.format(season=season, week=week)) or {}
        return self._cache[key]


def league_scoring(client: Any, league_id: str) -> dict[str, Any]:
    settings = load_settings(client, league_id)
    if settings.scoring_settings:
        return dict(settings.scoring_settings)
    rows = client.table("leagues").select("sleeper_league_id").eq("id", league_id).limit(1).execute().data or []
    sleeper_id = rows[0].get("sleeper_league_id") if rows else None
    return dict(fetch_sleeper_scoring(sleeper_id).get("scoring_settings") or {}) if sleeper_id else {}


# ---- model runners -------------------------------------------------------------------

def _params(req: Req, messages: list[dict[str, Any]] | None = None, *, web: bool | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"model": req.model, "max_tokens": 8000, "system": req.system,
                              "messages": messages or [{"role": "user", "content": req.prompt}]}
    if req.web_search if web is None else web:
        params["tools"] = [WEB_SEARCH_TOOL]
    return params


def _text(message: Any) -> str:
    return "\n".join(getattr(b, "text", "") for b in (getattr(message, "content", None) or []) if getattr(b, "type", "") == "text")


def _add(total: dict[str, int], more: dict[str, int]) -> dict[str, int]:
    return {k: total.get(k, 0) + more.get(k, 0) for k in set(total) | set(more)}


def sync_one(sdk: Any, req: Req, messages: list[dict[str, Any]] | None = None, usage: dict[str, int] | None = None) -> Res:
    """Answer now at full price; continues server-side search pauses and drops search if the org hasn't enabled it."""
    import anthropic

    web = req.web_search
    usage = dict(usage or {})
    messages = messages or [{"role": "user", "content": req.prompt}]
    for _ in range(5):
        try:
            resp = sdk.messages.create(**_params(req, messages, web=web))
        except anthropic.BadRequestError as exc:
            if web:
                print(f"[learning] web search unavailable ({str(exc)[:120]}); continuing without it", flush=True)
                web = False
                continue
            raise
        usage = _add(usage, response_usage(resp))
        if resp.stop_reason == "pause_turn":
            messages = [*messages, {"role": "assistant", "content": resp.content}]
            continue
        return Res(_text(resp), usage, batch=False)
    return Res("", usage, batch=False)


def make_runner(*, use_batch: bool = True, poll_seconds: int = 60, max_wait_seconds: int = 3 * 3600) -> Runner:
    import anthropic

    sdk = anthropic.Anthropic(api_key=api_key(), timeout=180, max_retries=3)

    def run_sync(reqs: list[Req]) -> dict[str, Res]:
        return {r.id: sync_one(sdk, r) for r in reqs}

    def run_batch(reqs: list[Req]) -> dict[str, Res]:
        if not reqs:
            return {}
        batch = sdk.messages.batches.create(requests=[{"custom_id": r.id, "params": _params(r)} for r in reqs])
        print(f"[learning] batch {batch.id}: {len(reqs)} requests submitted (half price)", flush=True)
        waited = 0
        while batch.processing_status != "ended":
            if waited >= max_wait_seconds:
                print(f"[learning] batch {batch.id} still running after {waited}s; answering the rest directly", flush=True)
                sdk.messages.batches.cancel(batch.id)
                return run_sync(reqs)
            time.sleep(poll_seconds)
            waited += poll_seconds
            batch = sdk.messages.batches.retrieve(batch.id)
        by_id = {r.id: r for r in reqs}
        out: dict[str, Res] = {}
        for item in sdk.messages.batches.results(batch.id):
            req = by_id.get(item.custom_id)
            if req is None:
                continue
            if item.result.type != "succeeded":
                print(f"[learning] batch item {item.custom_id} {item.result.type}; retrying directly", flush=True)
                out[req.id] = sync_one(sdk, req)
                continue
            msg = item.result.message
            usage = response_usage(msg)
            if msg.stop_reason == "pause_turn":  # a search paused mid-answer: finish it directly
                done = sync_one(sdk, req, [{"role": "user", "content": req.prompt}, {"role": "assistant", "content": msg.content}], usage)
                out[req.id] = Res(done.text, usage, batch=True)  # the batched part, at half price
                out[req.id + ":continuation"] = Res("", _sub(done.usage, usage), batch=False)  # the direct follow-up, full price
                continue
            out[req.id] = Res(_text(msg), usage, batch=True)
        for r in reqs:  # anything the batch dropped
            if r.id not in out:
                out[r.id] = sync_one(sdk, r)
        return out

    return run_batch if use_batch else run_sync


def _sub(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: max(0, a.get(k, 0) - b.get(k, 0)) for k in a}


# ---- the job ---------------------------------------------------------------------------

def run(client: Any, runner: Runner, *, season: int | None = None, web_search: bool = True, dry_run: bool = False,
        stats: WeeklyStats | None = None, now: datetime | None = None, main_model: str | None = None) -> dict[str, Any]:
    stats = stats or WeeklyStats()
    now = now or datetime.now(timezone.utc)
    main_model = main_model or load_config().model
    open_rows = client.table("league_ai_predictions").select("*").eq("status", "open").limit(2000).execute().data or []
    seasons = sorted({int(r["season"]) for r in open_rows} | ({season} if season else set()))
    done_by_season = {s: completed_week(client, s) for s in seasons}
    scoring_cache: dict[str, dict[str, Any]] = {}
    summary: dict[str, Any] = {"open": len(open_rows), "graded": 0, "ungradeable": 0, "waiting": 0, "leagues": [], "cost_usd": 0.0}

    # 1. score every due call in league scoring (no model needed)
    graded_items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pred in open_rows:
        if not is_due(pred, done_by_season.get(int(pred["season"]), 0)):
            summary["waiting"] += 1
            continue
        lid = str(pred["league_id"])
        if lid not in scoring_cache:
            scoring_cache[lid] = league_scoring(client, lid)
        scoring = scoring_cache[lid]
        s = int(pred["season"])
        if any(not stats.week(s, w) for w in grade_weeks(pred)):
            summary["waiting"] += 1
            continue  # Sleeper hasn't published that week's stats yet; try again tomorrow
        graded = grade(pred, lambda sid, w: league_points(stats.week(s, w).get(str(sid)), scoring)) if scoring else None
        if graded is None:
            summary["ungradeable"] += 1
            if not dry_run:
                client.table("league_ai_predictions").update({"status": "ungradeable", "graded_at": now.isoformat()}).eq("id", pred["id"]).execute()
            continue
        graded_items.append((pred, graded))

    # 2. diagnose: misses on the main model with web search, correct calls on Haiku
    reqs: list[Req] = []
    for pred, graded in graded_items:
        miss = not graded["correct"]
        web = web_search and miss
        system, prompt = diagnose_request(pred, graded, diagnostic_facts(client, pred), web_search=web)
        reqs.append(Req(id=str(pred["id"]), system=system, prompt=prompt, model=main_model if miss else REVIEW_MODEL, web_search=web,
                        feature="grading" if miss else "grading_review", league_id=str(pred["league_id"]), prediction_id=str(pred["id"])))
    answers = runner(reqs) if reqs else {}
    _record_all(client, reqs, answers, summary, dry_run)

    new_by_league: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pred, graded in graded_items:
        diag = parse_diagnosis(answers.get(str(pred["id"]), Res("")).text) or {}
        update = {**graded, "status": "graded", "graded_at": now.isoformat(),
                  "cause": diag.get("cause"), "process_error": diag.get("process_error"), "diagnosis": diag.get("diagnosis"), "lesson": diag.get("lesson")}
        update["result"] = {**graded["result"], "factor_review": diag.get("factor_review") or {}}
        summary["graded"] += 1
        print(f"[learning] {pred['kind']} {pred['season']} wk{pred['week']}: {'RIGHT' if graded['correct'] else 'WRONG'} "
              f"{graded['pick_points']} vs {graded['over_points']} | {diag.get('cause')} | {diag.get('lesson')}", flush=True)
        if not dry_run:
            client.table("league_ai_predictions").update(update).eq("id", pred["id"]).execute()
        new_by_league[str(pred["league_id"])].append({"kind": pred["kind"], "right": graded["correct"], "cause": diag.get("cause"),
                                                       "process_error": diag.get("process_error"), "factors": pred.get("factors"),
                                                       "factor_review": diag.get("factor_review"), "lesson": diag.get("lesson")})

    # 3. scorecard and playbook per league (one batched call per league)
    cards: dict[str, tuple[int, dict[str, Any], list[dict[str, Any]]]] = {}
    pb_reqs: list[Req] = []
    for lid, new_items in new_by_league.items():
        current_season = max(int(r["season"]) for r in open_rows if str(r["league_id"]) == lid)
        graded_rows = (client.table("league_ai_predictions").select("kind, factors, confidence, correct, cause").eq("league_id", lid)
                       .eq("season", current_season).eq("status", "graded").limit(5000).execute().data or [])
        card = scorecard(graded_rows)
        lessons = client.table("league_ai_lessons").select("*").eq("league_id", lid).eq("active", True).limit(100).execute().data or []
        cards[lid] = (current_season, card, lessons)
        system, prompt = consolidate_request(lessons, new_items, card)
        pb_reqs.append(Req(id=f"playbook:{lid}", system=system, prompt=prompt, model=main_model, feature="playbook", league_id=lid))
    pb_answers = runner(pb_reqs) if pb_reqs else {}
    _record_all(client, pb_reqs, pb_answers, summary, dry_run)

    for lid, (current_season, card, lessons) in cards.items():
        playbook = parse_playbook(pb_answers.get(f"playbook:{lid}", Res("")).text)
        summary["leagues"].append({"league_id": lid, "new": len(new_by_league[lid]), "scorecard": card, "lessons": len(playbook or [])})
        if dry_run:
            print(f"[learning] league {lid} scorecard {card}\n[learning] proposed playbook {playbook}", flush=True)
            continue
        client.table("league_ai_scorecards").upsert({"league_id": lid, "season": current_season, "stats": card, "computed_at": now.isoformat()},
                                                    on_conflict="league_id,season").execute()
        if playbook is not None:
            apply_playbook(client, lid, lessons, playbook, now)
    return summary


def _record_all(client: Any, reqs: list[Req], answers: dict[str, Res], summary: dict[str, Any], dry_run: bool) -> None:
    for r in reqs:
        for key, model, batch_ok in ((r.id, r.model, True), (r.id + ":continuation", r.model, False)):
            res = answers.get(key)
            if res is None or not res.usage:
                continue
            row = usage_row(league_id=r.league_id, user_id=None, feature=r.feature, model=model, batch=res.batch and batch_ok,
                            prediction_id=r.prediction_id, **res.usage)
            summary["cost_usd"] = round(summary["cost_usd"] + float(row["cost_usd"] or 0), 6)
            if not dry_run:
                record_usage(client, row)


def apply_playbook(client: Any, league_id: str, current: list[dict[str, Any]], playbook: list[dict[str, Any]], now: datetime) -> None:
    known = {str(l["id"]) for l in current}
    kept: set[str] = set()
    for item in playbook:
        row = {"lesson": item["lesson"], "kind": item["kind"], "factor": item["factor"], "evidence_count": item["evidence_count"],
               "active": item["active"], "updated_at": now.isoformat()}
        if item.get("id") and str(item["id"]) in known:
            kept.add(str(item["id"]))
            client.table("league_ai_lessons").update(row).eq("id", item["id"]).execute()
        elif item["active"]:
            client.table("league_ai_lessons").insert({**row, "league_id": league_id}).execute()
    for lid in known - kept:  # dropped from the playbook: retire, keep for history
        client.table("league_ai_lessons").update({"active": False, "updated_at": now.isoformat()}).eq("id", lid).execute()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-search", action="store_true")
    parser.add_argument("--no-batch", action="store_true")
    args = parser.parse_args(argv)
    client = service_client()
    cfg = load_config()
    if not cfg.api_key_present:
        print("[learning] ANTHROPIC_API_KEY is not set (add it as a GitHub Actions secret); nothing graded", flush=True)
        return 0
    try:
        summary = run(client, make_runner(use_batch=not args.no_batch), web_search=not args.no_search, dry_run=args.dry_run, main_model=cfg.model)
    except Exception as exc:
        if "league_ai_predictions" in str(exc) and ("PGRST205" in str(exc) or "schema cache" in str(exc)):
            print("[learning] tables not created yet (run migration 20261124_league_ai_learning.sql); nothing graded", flush=True)
            return 0
        raise
    print(f"[learning] done: {summary['graded']} graded, {summary['ungradeable']} ungradeable, {summary['waiting']} waiting on games; "
          f"model cost ${summary['cost_usd']:.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
