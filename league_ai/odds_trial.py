"""Playoff-odds trial: after each of the league's next 20 answers that used the playoff-odds simulation,
a small model checks how the answer would have read without the odds.

The owner still gets one answer, written with the odds. The check runs afterwards, off the owner's
clock, and is logged to league_ai_experiments. The weekly report (and `python -m league_ai.odds_trial`)
summarise the results: how often the odds changed the advice, and whether the version with them was better.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter
from typing import Any, Callable, Mapping

from league_ai.tools import ToolRegistry
from league_ai.usage import cost_usd, record as record_usage, response_usage, usage_row

EXPERIMENT = "playoff_odds_v1"
LIMIT = 20
MODEL = "claude-haiku-4-5"
ODDS_TOOLS = ("get_playoff_odds", "evaluate_trade")

SYSTEM = """You audit a fantasy football assistant GM. It answered an owner's question using a new input: playoff odds from a
simulation of the rest of the season. Judge what that input did to the answer.

Rebuild the answer the GM would have given WITHOUT the playoff odds (same other facts, just no odds), then compare.
Reply with JSON only:
{"changed": true|false,            // would the recommendation itself (do it / don't, start / sit, buy / sell) differ without the odds?
 "without_answer": "...",          // the answer without the odds, 2 sentences max
 "effect": "changed_call" | "added_context" | "no_effect",
 "better": "with" | "without" | "same",
 "why": "...",                     // one sentence: which version serves the owner better and why
 "risk": "..."}                    // one sentence: how the odds could mislead here (small sample, schedule, projections), or "none"
"""


def watch(tools: ToolRegistry) -> list[dict[str, Any]]:
    """Record what the odds tools return during one answer. Returns the list the outputs land in."""
    seen: list[dict[str, Any]] = []
    for name in ODDS_TOOLS:
        spec = tools.tools.get(name)
        if spec is None:
            continue
        inner = spec.fn

        def wrapped(*args: Any, _inner: Callable[..., Any] = inner, _name: str = name, **kwargs: Any) -> Any:
            out = _inner(*args, **kwargs)
            odds = out.get("playoff_odds") if _name == "evaluate_trade" and isinstance(out, Mapping) else out
            if isinstance(odds, Mapping) and ("odds" in odds or "you" in odds):
                seen.append({"tool": _name, "args": kwargs, "odds": dict(odds)})
            return out
        spec.fn = wrapped
    return seen


def count(client: Any, league_id: str) -> int:
    try:
        return int(client.rpc("league_ai_experiment_count", {"p_league_id": league_id, "p_experiment": EXPERIMENT}).execute().data or 0)
    except Exception:
        return LIMIT  # table not there yet, or no access: the trial stays off


def parse(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        out = json.loads(re.sub(r"//[^\n]*", "", match.group(0)))
    except Exception:
        return None
    return out if isinstance(out, dict) and "changed" in out else None


def prompt(question: str, answer: str, seen: list[Mapping[str, Any]]) -> str:
    odds = json.dumps([{k: v for k, v in s.items() if k != "args"} | {"asked_with": s.get("args")} for s in seen], default=str)[:6000]
    return f"Owner's question:\n{question.strip()[:2000]}\n\nGM's answer (written with the odds):\n{answer.strip()[:6000]}\n\nPlayoff odds the GM saw:\n{odds}"


def check(question: str, answer: str, seen: list[Mapping[str, Any]], sdk: Any) -> tuple[dict[str, Any] | None, dict[str, int]]:
    resp = sdk.messages.create(model=MODEL, max_tokens=600, system=SYSTEM, messages=[{"role": "user", "content": prompt(question, answer, seen)}])
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    return parse(text), response_usage(resp)


def maybe_run(client: Any, *, league_id: str, user_id: str, conversation_id: str | None, question: str, answer: str,
              seen: list[Mapping[str, Any]], sdk_factory: Callable[[], Any], background: bool = True) -> bool:
    """Start the check when this answer used the odds and the league still has trial slots. Never raises."""
    if not seen or not answer or count(client, league_id) >= LIMIT:
        return False

    def run() -> None:
        try:
            result, tokens = check(question, answer, seen, sdk_factory())
            record_usage(client, usage_row(league_id=league_id, user_id=user_id, feature="experiment", model=MODEL, conversation_id=conversation_id, **tokens))
            cost = cost_usd(MODEL, **tokens)
            if result is None:
                return
            client.table("league_ai_experiments").insert({
                "league_id": league_id, "user_id": user_id, "experiment": EXPERIMENT, "conversation_id": conversation_id,
                "question": question[:2000], "result": {**result, "odds_seen": [s.get("odds") for s in seen][:3]}, "cost_usd": cost,
            }).execute()
        except Exception as exc:
            print(f"ODDS_TRIAL_ERROR {type(exc).__name__}: {str(exc)[:200]}", flush=True)

    if background:
        threading.Thread(target=run, daemon=True).start()
    else:
        run()
    return True


def summarize(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    results = [r.get("result") or {} for r in rows]
    n = len(results)
    effects = Counter(str(x.get("effect") or "unknown") for x in results)
    better = Counter(str(x.get("better") or "unknown") for x in results)
    return {
        "checks": n, "of": LIMIT,
        "changed_call": sum(1 for x in results if x.get("changed") is True),
        "effects": dict(effects), "better": dict(better),
        "cost": round(sum(float(r.get("cost_usd") or 0) for r in rows), 4),
        "verdict": _verdict(n, better, effects),
        "items": [{"when": str(r.get("created_at"))[:16], "changed": x.get("changed"), "effect": x.get("effect"), "better": x.get("better"),
                   "why": x.get("why"), "risk": x.get("risk"), "without_answer": x.get("without_answer"), "question": r.get("question")}
                  for r, x in zip(rows, results)],
    }


def _verdict(n: int, better: Counter, effects: Counter) -> str:
    if not n:
        return "No answers have used the playoff odds yet."
    w, wo = better.get("with", 0), better.get("without", 0)
    lean = "helped" if w > wo else "hurt" if wo > w else "made no clear difference"
    tail = "" if n >= LIMIT else f" (only {n} of {LIMIT} checks so far)"
    return f"The odds {lean}: better with them {w}×, without {wo}×, same {better.get('same', 0)}×; they changed the call {effects.get('changed_call', 0)}×{tail}."


def load(client: Any, league_id: str) -> list[dict[str, Any]]:
    try:
        return (client.table("league_ai_experiments").select("created_at, question, result, cost_usd").eq("league_id", league_id)
                .eq("experiment", EXPERIMENT).order("created_at").limit(200).execute().data or [])
    except Exception:
        return []


def main() -> int:
    """Print the trial results for every league (service role), without question text unless REPORT_DETAIL=full."""
    import os

    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    full = (os.environ.get("REPORT_DETAIL") or "").lower() == "full"
    for league in client.table("leagues").select("id, name").execute().data or []:
        rows = load(client, str(league["id"]))
        if not rows:
            continue
        s = summarize(rows)
        print(f"== {league.get('name')}: {s['verdict']} Cost ${s['cost']:.2f}")
        for i, item in enumerate(s["items"], 1):
            print(f"{i:>2}. {item['when']} changed={item['changed']} effect={item['effect']} better={item['better']}")
            if full:  # the reasons can name players and trades from owners' private chats
                print(f"    question: {item['question']}\n    without the odds: {item['without_answer']}\n    why: {item['why']}\n    risk: {item['risk']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
