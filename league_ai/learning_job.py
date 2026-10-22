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
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from pipeline.nfl_data.common import service_client  # also puts the repo root on sys.path

import requests

from league_ai.config import api_key, load_config
from league_ai.learning import (
    consolidate_request, diagnose_request, diagnostic_facts, grade, grade_weeks, is_due, league_points, parse_diagnosis, parse_playbook, play_checks, scorecard,
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


def played_in(week_stats: Mapping[str, Any], sleeper_id: str) -> bool | None:
    """Whether he took the field that week by Sleeper's stats (None when the week's stats aren't in)."""
    if not week_stats:
        return None
    st = week_stats.get(str(sleeper_id)) or {}
    return bool(st.get("gp") or st.get("off_snp"))


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


def make_runner(*, use_batch: bool = True, poll_seconds: int = 60, max_wait_seconds: int = 90 * 60) -> Runner:
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
        stats: WeeklyStats | None = None, now: datetime | None = None, main_model: str | None = None,
        playbook_runner: Runner | None = None, rebuild: bool = False, backtest: bool | None = None) -> dict[str, Any]:
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
        if graded is not None:
            checks = play_checks(pred, lambda sid, w: played_in(stats.week(s, w), sid))
            if checks:
                graded["result"]["play_checks"] = checks
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

    # 3. scorecard and playbook per league. The scorecard is saved first (no model needed), so a slow or failed
    # playbook call can never leave the chat without its record. Leagues with graded calls but no lessons yet
    # (a playbook step that never finished, or --rebuild) get their playbook rebuilt from every graded call.
    playbook_runner = playbook_runner or runner
    cards: dict[str, tuple[int, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for lid, current_season in _leagues_to_review(client, open_rows, new_by_league, season, rebuild):
        graded_rows = (client.table("league_ai_predictions").select("kind, factors, confidence, correct, cause, process_error, result, lesson").eq("league_id", lid)
                       .eq("season", current_season).eq("status", "graded").limit(5000).execute().data or [])
        if not graded_rows:
            continue
        card = scorecard(graded_rows)
        lessons = client.table("league_ai_lessons").select("*").eq("league_id", lid).eq("active", True).limit(100).execute().data or []
        new_items = new_by_league.get(lid) or []
        if rebuild or not lessons:  # rebuild from every graded call, not just the ones graded in this run
            new_items = [{"kind": r.get("kind"), "right": r.get("correct"), "cause": r.get("cause"), "process_error": r.get("process_error"),
                          "factors": r.get("factors"), "factor_review": (r.get("result") or {}).get("factor_review"), "lesson": r.get("lesson")} for r in graded_rows]
        if not new_items:
            continue
        cards[lid] = (current_season, card, lessons, new_items)
        if dry_run:
            print(f"[learning] league {lid} scorecard {card}", flush=True)
            continue
        client.table("league_ai_scorecards").upsert({"league_id": lid, "season": current_season, "stats": card, "computed_at": now.isoformat()},
                                                    on_conflict="league_id,season").execute()

    pb_reqs: list[Req] = []
    for lid, (_, card, lessons, new_items) in cards.items():
        system, prompt = consolidate_request(lessons, new_items, card)
        pb_reqs.append(Req(id=f"playbook:{lid}", system=system, prompt=prompt, model=main_model, feature="playbook", league_id=lid))
    pb_answers = playbook_runner(pb_reqs) if pb_reqs else {}
    _record_all(client, pb_reqs, pb_answers, summary, dry_run)

    for lid, (_, card, lessons, new_items) in cards.items():
        playbook = parse_playbook(pb_answers.get(f"playbook:{lid}", Res("")).text)
        summary["leagues"].append({"league_id": lid, "new": len(new_by_league.get(lid) or []), "scorecard": card, "lessons": len(playbook or [])})
        if dry_run:
            print(f"[learning] league {lid} proposed playbook {playbook}", flush=True)
            continue
        if playbook is not None:
            apply_playbook(client, lid, lessons, playbook, now)
        else:
            print(f"[learning] league {lid}: playbook answer unreadable; lessons unchanged", flush=True)
    try:
        summary["trade_outcomes"] = track_trade_outcomes(client, now=now, dry_run=dry_run)
    except Exception as exc:  # never let outcome tracking break grading
        print(f"[learning] trade outcome tracking skipped: {type(exc).__name__}: {exc}", flush=True)
    if backtest if backtest is not None else now.weekday() == 1:  # weekly, with the Tuesday report
        try:
            summary["trade_backtest"] = run_trade_backtests(client, now=now, dry_run=dry_run)
        except Exception as exc:
            print(f"[learning] trade backtest skipped: {type(exc).__name__}: {exc}", flush=True)
    return summary


def run_trade_backtests(client: Any, *, now: datetime, dry_run: bool = False) -> dict[str, Any]:
    """Replay every completed trade per league, report the engine's hit rate, and save each owner's revealed lean. No model calls."""
    from league_ai.trade_backtest import backtest, load_trades, save_styles

    out: dict[str, Any] = {}
    leagues = {str(r["league_id"]) for r in (client.table("league_history_events").select("league_id").eq("kind", "trade").limit(5000).execute().data or [])}
    for lid in sorted(leagues):
        result = backtest(load_trades(client, lid))
        out[lid] = {k: result[k] for k in ("trades", "graded", "hit_rate")}
        print(f"[learning] trade backtest {lid}: {result['graded']} trades graded, hit rate {result['hit_rate']}, leans {result['owner_now_weight']}", flush=True)
        if not dry_run:
            save_styles(client, lid, result, now)
    return out


NOT_MADE_AFTER_DAYS = 10


def _ids(players: Any) -> set[str]:
    return {str(p.get("sleeper_id")) for p in players or [] if isinstance(p, Mapping) and p.get("sleeper_id")}


def track_trade_outcomes(client: Any, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    """Did the trades the AI pitched or judged happen? Sleeper only publishes completed trades, so a logged trade call
    is 'accepted' when a completed Sleeper trade moved one of its incoming players and one of its outgoing players
    together after it was asked, and 'not_completed' when nothing like it happened within NOT_MADE_AFTER_DAYS.
    Declines and counters come from chat (Ledger.record_trade_response). No model calls."""
    now = now or datetime.now(timezone.utc)
    calls = (client.table("league_ai_predictions").select("id, league_id, user_id, pick, over, partner, created_at")
             .eq("kind", "trade").in_("status", ["open", "graded"]).gte("created_at", (now - timedelta(days=120)).isoformat())
             .limit(2000).execute().data or [])
    out = {"accepted": 0, "not_completed": 0}
    if not calls:
        return out
    done = client.table("league_ai_trade_outcomes").select("prediction_id, outcome").in_("prediction_id", [c["id"] for c in calls]).limit(5000).execute().data or []
    settled = {str(d["prediction_id"]) for d in done if d.get("outcome") in ("accepted", "not_completed")}
    trades_by_league: dict[str, dict[str, dict[str, Any]]] = {}
    for c in calls:
        if str(c["id"]) in settled:
            continue
        lid = str(c["league_id"])
        if lid not in trades_by_league:
            rows = (client.table("league_history_events").select("event_id, player_id, occurred_at").eq("league_id", lid).eq("kind", "trade")
                    .gte("occurred_at", (now - timedelta(days=130)).isoformat()).limit(5000).execute().data or [])
            by_tid: dict[str, dict[str, Any]] = {}
            for r in rows:
                parts = str(r.get("event_id") or "").split(":")
                if len(parts) < 2 or parts[0] != "sleeper" or not r.get("player_id"):
                    continue
                t = by_tid.setdefault(parts[1], {"players": set(), "occurred_at": r.get("occurred_at")})
                t["players"].add(str(r["player_id"]))
            trades_by_league[lid] = by_tid
        asked = _parse_ts(c.get("created_at")) or now
        gets, gives = _ids(c.get("pick")), _ids(c.get("over"))
        hit = None
        for tid, t in trades_by_league[lid].items():
            when = _parse_ts(t["occurred_at"])
            if when and when >= asked - timedelta(days=1) and t["players"] & gets and t["players"] & gives:
                hit = (tid, t["occurred_at"])
                break
        base = {"league_id": lid, "user_id": c["user_id"], "prediction_id": c["id"], "partner": c.get("partner"),
                "receives": c.get("pick") or [], "sends": c.get("over") or []}
        if hit:
            row = {**base, "outcome": "accepted", "source": "sleeper", "sleeper_transaction_id": hit[0], "occurred_at": hit[1]}
        elif now - asked >= timedelta(days=NOT_MADE_AFTER_DAYS):
            row = {**base, "outcome": "not_completed", "source": "inferred", "occurred_at": now.isoformat()}
        else:
            continue
        out[row["outcome"]] += 1
        print(f"[learning] trade call {c['id']}: {row['outcome']}" + (f" (Sleeper trade {hit[0]})" if hit else ""), flush=True)
        if not dry_run:
            client.table("league_ai_trade_outcomes").insert(row).execute()
    return out


def _parse_ts(v: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _leagues_to_review(client: Any, open_rows: list[dict[str, Any]], new_by_league: Mapping[str, Any], season: int | None, rebuild: bool) -> list[tuple[str, int]]:
    """(league, season) pairs whose scorecard and playbook need writing: any league with new grades, plus
    every league with graded calls this season but no active lessons (or all of them on --rebuild)."""
    seasons: dict[str, int] = {}
    for r in open_rows:
        lid = str(r["league_id"])
        seasons[lid] = max(seasons.get(lid, 0), int(r["season"]))
    try:
        graded = client.table("league_ai_predictions").select("league_id, season").eq("status", "graded").limit(5000).execute().data or []
    except Exception:
        graded = []
    latest = max([int(r["season"]) for r in graded] + ([season] if season else []), default=0)
    with_grades = {str(r["league_id"]) for r in graded if int(r["season"]) == latest}
    out = {lid: seasons.get(lid, latest) for lid in new_by_league}
    for lid in with_grades - set(out):
        if rebuild:
            out[lid] = latest
            continue
        try:
            has_lessons = client.table("league_ai_lessons").select("id").eq("league_id", lid).eq("active", True).limit(1).execute().data
        except Exception:
            has_lessons = True
        if not has_lessons:
            out[lid] = latest
    return sorted(out.items())


def repair(client: Any, *, dry_run: bool = False) -> dict[str, int]:
    """One-off cleanup after the grading fixes: void restated duplicate calls, and reopen graded start/sit and
    pickup calls with several alternatives (scored on the old average-of-alternatives rule) so the next run
    regrades them against the best alternative."""
    rows = client.table("league_ai_predictions").select("*").in_("status", ["open", "graded"]).limit(5000).execute().data or []
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)  # newest first: the newest call of a restated pair counts

    def ids(players: Any) -> frozenset[str]:
        return frozenset(str(p.get("sleeper_id") or p.get("name")) for p in players or [])

    kept: list[dict[str, Any]] = []
    voided = reopened = 0
    for r in rows:
        dup = next((k for k in kept if k["league_id"] == r["league_id"] and k.get("user_id") == r.get("user_id") and k["kind"] == r["kind"]
                    and k["season"] == r["season"] and (r["kind"] == "trade" or k["week"] == r["week"])
                    and ids(k["pick"]) == ids(r["pick"]) and ids(k["over"]) & ids(r["over"])), None)
        if dup is not None:
            voided += 1
            if not dry_run:
                client.table("league_ai_predictions").update({"status": "void"}).eq("id", r["id"]).execute()
            continue
        kept.append(r)
        if r["status"] == "graded" and r["kind"] != "trade" and len(r.get("over") or []) > 1:
            reopened += 1
            if not dry_run:
                client.table("league_ai_predictions").update({
                    "status": "open", "graded_at": None, "correct": None, "pick_points": None, "over_points": None, "margin": None,
                    "result": None, "cause": None, "process_error": None, "diagnosis": None, "lesson": None}).eq("id", r["id"]).execute()
    print(f"[learning] repair: {voided} duplicate call(s) voided, {reopened} multi-alternative call(s) reopened for regrading", flush=True)
    return {"voided": voided, "reopened": reopened}


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
    parser.add_argument("--rebuild", action="store_true", help="rebuild every league's scorecard and playbook from all graded calls")
    parser.add_argument("--repair", action="store_true", help="void duplicate calls and regrade multi-alternative calls first")
    parser.add_argument("--backtest", action="store_true", help="replay every completed league trade through the trade engine now (otherwise Tuesdays)")
    args = parser.parse_args(argv)
    client = service_client()
    cfg = load_config()
    if not cfg.api_key_present:
        print("[learning] ANTHROPIC_API_KEY is not set (add it as a GitHub Actions secret); nothing graded", flush=True)
        return 0
    try:
        if args.repair:
            repair(client, dry_run=args.dry_run)
        # the playbook is one call per league per week: answer it directly, since the batch discount saves under a cent
        # and a batch that outlives the job is how the playbook went unwritten
        summary = run(client, make_runner(use_batch=not args.no_batch), web_search=not args.no_search, dry_run=args.dry_run, main_model=cfg.model,
                      playbook_runner=make_runner(use_batch=False), rebuild=args.rebuild, backtest=True if args.backtest else None)
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
