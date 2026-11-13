"""Answer test set: ask the Assistant GM a fixed mix of real questions and grade the written answers. Costs money.

The practice test (league_ai.practice_test) grades the engine's numbers for free. This grades what owners read:

  * facts   : the fact checker (league_ai.fact_check) counts statements the data contradicts, per answer
  * rubric  : free checks of what each kind of answer must have (start/sit quotes both projections, a trade gives an
              opening offer and a walk-away, short first answers, no headings, used the right tool)
  * compare : with EVAL_COMPARE set (e.g. claude-opus-5-5), the trade, contract and strategy questions are also answered
              by that model, and a blind judge picks the more accurate and useful answer of each pair

Questions are built from this week's real rosters, the same way for every run. Nothing is written to owners' chats,
notes or track records. The public Actions log gets numbers only; the full answers are saved to
league_ai_experiments ('answer_eval_v1') under the system user.

    EVAL_BUDGET=4 EVAL_COMPARE=claude-opus-5-5 python -m league_ai.answer_eval
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import uuid
from collections import defaultdict
from typing import Any, Mapping

EXPERIMENT = "answer_eval_v1"
SYSTEM_USER = "00000000-0000-0000-0000-000000000000"
STAND_IN = "00000000-0000-0000-0000-00000000beef"
BUDGET = float(os.environ.get("EVAL_BUDGET", "4") or 4)
MAX_ANSWER = 0.35  # the most one answer (plus its check) should cost; the run stops before the budget could be crossed
WRITE_TOOLS = ("remember", "forget", "record_trade_response", "log_prediction")
JUDGE_MODEL = "claude-haiku-5-5"
# how many of each kind of question
MIX = {"start_sit": 6, "trade_find": 3, "trade_target": 3, "buy_sell": 2, "contract": 2, "pickup": 2, "playoffs": 1, "owner_style": 1}
HARD = ("trade_find", "trade_target", "buy_sell", "contract")  # answered by EVAL_COMPARE too


def candidates(mine: Mapping[str, Any], rankings: list[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Questions this owner could be asked, best first, by kind (closest start/sit calls first)."""
    lineup = [dict(p) for p in mine.get("week_lineup") or []]
    bench = [dict(p) for p in mine.get("week_bench_detail") or [] if (p.get("pts") or 0) > 0]
    weak = str(mine.get("weakest_slot") or "RB")
    wpos = weak.upper().replace("SUPER_FLEX", "QB").replace("FLEX", "RB")
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pairs = sorted(((abs(float(s.get("pts") or 0) - float(b.get("pts") or 0)), s, b) for b in bench for s in lineup
                    if str(s.get("pos")) == str(b.get("pos")) and s.get("id") != b.get("id")), key=lambda x: x[0])
    seen: set[str] = set()
    for _, s, b in pairs:
        if s["id"] in seen or b["id"] in seen:
            continue
        seen |= {s["id"], b["id"]}
        out["start_sit"].append({"q": f"Should I start {s['name']} or {b['name']} this week?"})
    out["trade_find"].append({"q": f"Find me a trade that upgrades my {wpos} without hurting my lineup. What should I offer first?"})
    targets = sorted(((float(p.get("pts") or 0), p, r) for r in rankings if r.get("team_id") != mine.get("team_id")
                      for p in r.get("week_lineup") or [] if str(p.get("pos")) == wpos), key=lambda x: -x[0])
    for _, p, r in targets[:1]:
        out["trade_target"].append({"q": f"What would it take to get {p['name']} from {r['owner']}? Give me an opening offer and where to walk away."})
    top = sorted(lineup, key=lambda p: -float(p.get("pts") or 0))
    if len(top) > 1:
        out["buy_sell"].append({"q": f"Should I sell {top[1]['name']} now or hold him?"})
    out["contract"].append({"q": "Which of my contracts are the best and worst values, and who should I keep or cut before next season?"})
    out["pickup"].append({"q": f"Who is the best free agent I could add at {wpos}, who would I drop, and what should I bid?"})
    out["playoffs"].append({"q": "What are my playoff odds, and what should I do the rest of the season to get in?"})
    others = [r for r in rankings if r.get("team_id") != mine.get("team_id")]
    if others:
        out["owner_style"].append({"q": f"How does {others[len(others) // 2]['owner']} usually trade, and how should I pitch them?"})
    return out


def plan(per_team: list[tuple[Any, dict[str, list[dict[str, Any]]]]]) -> list[tuple[Any, str, dict[str, Any]]]:
    """Each kind's quota spread over owners round robin, grouped by owner so each owner's pack stays cached."""
    picked: dict[int, list[tuple[str, dict[str, Any]]]] = {i: [] for i in range(len(per_team))}
    for k, (kind, want) in enumerate(MIX.items()):
        depth = [0] * len(per_team)
        i = 3 * k
        for _ in range(len(per_team) * 4):
            if not want:
                break
            j = i % len(per_team)
            cands = per_team[j][1].get(kind) or []
            if depth[j] < len(cands):
                picked[j].append((kind, cands[depth[j]]))
                depth[j] += 1
                want -= 1
            i += 1
    return [(per_team[j][0], kind, q) for j, items in picked.items() for kind, q in items]


# ---- grading -----------------------------------------------------------------------------------

_NUM = re.compile(r"\d+\.\d")


def rubric(kind: str, answer: str, tools: list[str]) -> dict[str, bool]:
    """Free checks of what the answer must have."""
    text = answer or ""
    low = text.lower()
    words = len(text.split())
    out = {"short": words <= 140, "no_headings": not any(line.lstrip().startswith("#") for line in text.splitlines()),
           "has_numbers": bool(re.search(r"\d", text))}
    if kind == "start_sit":
        out["used_engine"] = "compare_players" in tools
        out["quotes_projections"] = len(_NUM.findall(text)) >= 2
    if kind in ("trade_find", "trade_target"):
        out["used_finder"] = any(t in tools for t in ("find_trade_targets", "evaluate_trade"))
        out["has_ladder"] = ("open" in low) and any(w in low for w in ("walk", "most", "max", "top out", "ceiling", "stop at"))
    if kind == "buy_sell":
        out["clear_verdict"] = any(w in low[:160] for w in ("sell", "hold", "keep", "buy"))
    return out


JUDGE_SYSTEM = """You compare two answers a fantasy football co-GM gave an owner to the same question, with the data it had.
Pick the one that is more accurate (numbers match the data, nothing invented) and more useful (a clear call, the
right reasons, a concrete next step, short enough to read on a phone). Ignore style otherwise. Answer with JSON."""
JUDGE_SCHEMA = {"type": "object", "properties": {"better": {"type": "string", "enum": ["A", "B", "tie"]}, "why": {"type": "string"}},
                "required": ["better", "why"], "additionalProperties": False}


def judge(sdk: Any, question: str, data: str, a: str, b: str) -> tuple[str, dict[str, int]]:
    from league_ai.fact_check import _usage

    resp = sdk.messages.create(model=JUDGE_MODEL, max_tokens=800, system=JUDGE_SYSTEM,
                               messages=[{"role": "user", "content": f"QUESTION\n{question}\n\nDATA\n{data[:40000]}\n\nANSWER A\n{a}\n\nANSWER B\n{b}"}],
                               output_config={"effort": "low", "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}})
    raw = "".join(getattr(x, "text", "") for x in resp.content if getattr(x, "type", "") == "text")
    return str(json.loads(raw).get("better") or "tie"), _usage(resp)


# ---- run ---------------------------------------------------------------------------------------

def _state(client: Any, league_id: str, season: int) -> dict[str, Any]:
    from league_ai.practice_test import _state as practice_state

    return practice_state(client, league_id, season)


def _quiet(tools: Any) -> list[str]:
    """Swap write tools for no-ops and record which tools ran."""
    names: list[str] = []
    for name, spec in tools.tools.items():
        inner = spec.fn

        def run(_n=name, _inner=inner, **kw: Any) -> Any:
            names.append(_n)
            return {"logged": True} if _n in WRITE_TOOLS else _inner(**kw)
        spec.fn = run
    return names


def main() -> int:
    import anthropic
    from supabase import create_client

    from api import front_office as fo_api
    from league_ai import fact_check, runtime
    from league_ai.config import api_key, load_config
    from league_ai.front_office import week_context
    from league_ai.routing import effort_for
    from league_ai.service import LeagueDataSources, answer
    from league_ai.sleeper_roster import fill_from_sleeper
    from league_ai.usage import cost_usd

    os.environ["LEAGUE_AI_IDEA_LOG"] = "off"
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    sdk = anthropic.Anthropic(api_key=api_key(), timeout=120.0, max_retries=2)
    seasons = client.table("league_seasons").select("league_id, season").eq("is_active", True).order("season", desc=True).limit(1).execute().data or []
    if not seasons:
        print("no active league season")
        return 0
    lid, season = str(seasons[0]["league_id"]), int(seasons[0]["season"])
    league = next(iter(client.table("leagues").select("id, name, sleeper_league_id").eq("id", lid).limit(1).execute().data or []), {})
    sid = str(league.get("sleeper_league_id") or "")
    base_model = load_config().model
    compare = (os.environ.get("EVAL_COMPARE") or "").strip() or None
    shared = fill_from_sleeper(client, _state(client, lid, season), sid, lid) if sid else _state(client, lid, season)
    teams = client.table("league_teams").select("id, owner_name, team_name, sleeper_roster_id").eq("league_id", lid).execute().data or []
    sessions, per_team = {}, []
    for t in teams:
        if t.get("sleeper_roster_id") is None:
            continue
        o = runtime.Owner(user_id=str(uuid.uuid5(uuid.UUID(STAND_IN), str(t["id"]))), league_id=lid, league_team_id=str(t["id"]), season=season,
                          owner_name=t.get("owner_name") or "Owner", team_name=t.get("team_name") or "Team")
        try:
            s = fo_api.Session(client, o, sid)
            s.state = lambda: shared
            fo = s.front_office()
            mine = next((r for r in fo.get("rankings") or [] if str(r.get("team_id")) == o.league_team_id), None)
            if mine:
                sessions[o.league_team_id] = (s, fo)
                per_team.append((o, candidates(mine, fo.get("rankings") or [])))
        except Exception as exc:
            print(f"skip an owner: {type(exc).__name__}", flush=True)
    queue = plan(per_team)
    print(f"{len(per_team)} owners, {len(queue)} questions, model {base_model}, compare {compare or 'off'}, budget ${BUDGET:.2f}", flush=True)

    spent = 0.0
    items: list[dict[str, Any]] = []

    def ask(o: Any, kind: str, q: str, model: str | None) -> dict[str, Any]:
        nonlocal spent
        s, fo = sessions[o.league_team_id]
        tools = runtime.chat_tools(client, o, state=s.state(), rules=s.rules(), league=s.league, week=s.week(), positions=s.positions,
                                   playoff_week=s.playoff_start, question=lambda: q, conversation_id=lambda: None,
                                   odds=lambda: fo_api._gm_odds(s, fo), extra=[fo_api.matchup_tool(s, fo)])
        ran = _quiet(tools)
        cfg = load_config()
        if model:
            from dataclasses import replace
            cfg = replace(cfg, model=model)
        t0 = time.monotonic()
        wc = week_context(fo, o.owner_name)
        res = answer(question=q, history=[], asker=o.asker(), sources=LeagueDataSources(client=client, league_id=lid, season=season), tools=tools,
                     client=runtime.ClaudeClient(cfg), pack=s.pack(), week_context=runtime.with_question_focus(wc, q), turn_effort=effort_for(q))
        tr = res.trace.as_dict()
        cost = cost_usd(tr.get("model") or cfg.model, input_tokens=tr["input_tokens"], output_tokens=tr["output_tokens"],
                        cache_read_tokens=tr["cache_read_tokens"], cache_write_tokens=tr["cache_creation_tokens"]) or 0.0
        fc = fact_check.check(res.text, pack=res.pack or s.pack(), evidence=res.evidence, week_context=wc, sdk_client=sdk) if res.ok else None
        if fc and fc.ran:
            cost += cost_usd(fact_check.MODEL, **fc.usage) or 0.0
        spent += cost
        return {"model": cfg.model, "ok": res.ok, "error": res.error_code, "answer": res.text, "cost": round(cost, 4), "seconds": round(time.monotonic() - t0, 1),
                "tools": list(ran), "facts_wrong": len(fc.problems) if fc and fc.ran else None, "problems": fc.problems if fc else [],
                "rubric": rubric(kind, res.text, list(ran)) if res.ok else {}, "data": "\n".join(f"{e.get('tool')}: {str(e.get('result'))[:3000]}" for e in res.evidence)}

    for n, (o, kind, q) in enumerate(queue, 1):
        runs = [None] + ([compare] if compare and kind in HARD else [])
        if spent + MAX_ANSWER * len(runs) > BUDGET:
            print(f"budget stop before question {n}: spent ${spent:.2f}", flush=True)
            break
        item: dict[str, Any] = {"n": n, "kind": kind, "question": q["q"], "owner": o.owner_name, "answers": []}
        for model in runs:
            try:
                item["answers"].append(ask(o, kind, q["q"], model))
            except Exception as exc:
                item["answers"].append({"model": model or base_model, "ok": False, "error": type(exc).__name__})
        if len(item["answers"]) == 2 and all(a.get("ok") for a in item["answers"]):
            flip = random.Random(n).random() < 0.5
            a, b = item["answers"][::-1] if flip else item["answers"]
            try:
                better, usage = judge(sdk, q["q"], a.get("data") or "", a["answer"], b["answer"])
                spent += cost_usd(JUDGE_MODEL, **usage) or 0.0
                winner = {"A": a, "B": b}.get(better)
                item["winner"] = winner["model"] if winner else "tie"
            except Exception as exc:
                item["winner"] = f"judge failed ({type(exc).__name__})"
        items.append(item)
        line = " ".join(f"{(x.get('model') or '')[-10:]} ok={x.get('ok')} wrong={x.get('facts_wrong')} ${x.get('cost', 0):.3f}" for x in item["answers"])
        print(f"{n:>2}. {kind:<12} {line}{' winner=' + str(item.get('winner'))[-10:] if item.get('winner') else ''} total ${spent:.2f}", flush=True)

    summary = summarize(items)
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    try:
        client.table("league_ai_experiments").insert({"league_id": lid, "user_id": SYSTEM_USER, "experiment": EXPERIMENT,
                                                      "question": f"answer eval {time.strftime('%Y-%m-%d')}",
                                                      "result": {"summary": summary, "items": items}, "cost_usd": round(spent, 4)}).execute()
    except Exception as exc:
        print(f"save failed: {type(exc).__name__}", flush=True)
    return 0


def summarize(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Per model: answers, share with no wrong facts, wrong facts per answer, rubric pass rate, cost and time; pairwise wins."""
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for it in items:
        for a in it.get("answers") or []:
            by[str(a.get("model"))].append({**a, "kind": it.get("kind")})
    out: dict[str, Any] = {}
    for model, rows in by.items():
        ok = [r for r in rows if r.get("ok")]
        checked = [r for r in ok if r.get("facts_wrong") is not None]
        checks = [v for r in ok for v in (r.get("rubric") or {}).values()]
        out[model] = {"answers": len(rows), "ok": len(ok),
                      "clean_share": round(sum(1 for r in checked if not r["facts_wrong"]) / len(checked), 3) if checked else None,
                      "wrong_per_answer": round(sum(r["facts_wrong"] for r in checked) / len(checked), 3) if checked else None,
                      "rubric_pass": round(sum(checks) / len(checks), 3) if checks else None,
                      "avg_cost": round(sum(r.get("cost") or 0 for r in ok) / len(ok), 4) if ok else None,
                      "avg_seconds": round(sum(r.get("seconds") or 0 for r in ok) / len(ok), 1) if ok else None,
                      "hard_only": _hard(rows)}
    wins: dict[str, int] = defaultdict(int)
    for it in items:
        if it.get("winner"):
            wins[str(it["winner"])] += 1
    if wins:
        out["pairwise_wins"] = dict(wins)
    return out


def _hard(rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    hard = [r for r in rows if r.get("kind") in HARD and r.get("ok") and r.get("facts_wrong") is not None]
    if not hard:
        return None
    checks = [v for r in hard for v in (r.get("rubric") or {}).values()]
    return {"answers": len(hard), "wrong_per_answer": round(sum(r["facts_wrong"] for r in hard) / len(hard), 3),
            "rubric_pass": round(sum(checks) / len(checks), 3) if checks else None, "avg_cost": round(sum(r.get("cost") or 0 for r in hard) / len(hard), 4)}


if __name__ == "__main__":
    sys.exit(main())
