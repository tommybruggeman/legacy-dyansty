"""Stress test for the Assistant GM: ask it a batch of real questions for every owner, under a hard budget.

`python -m league_ai.stress_test ask` builds questions from this week's real rosters (close start/sit
calls, pickups, trades, playoff odds), runs each through the same Assistant GM the app uses, and saves
the question, answer, every tool call and the cost to stress_results.json. It stops before the budget
(STRESS_BUDGET, default $5) runs out. Nothing is written to owners' chats, notes or track records:
the tools that write are swapped for ones that only record what the GM wanted to log.

`python -m league_ai.stress_test grade` scores the calls the GM logged against real league points once
the games are final (start/sit after this week, pickups and trades over their horizon).
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, Mapping

RESULTS = os.environ.get("STRESS_RESULTS", "stress_results.json")
BUDGET = float(os.environ.get("STRESS_BUDGET", "5") or 5)
QUESTIONS = int(os.environ.get("STRESS_QUESTIONS", "50") or 50)
# the most one answer has cost; the run stops when the next answer could cross the budget
MAX_ANSWER = float(os.environ.get("STRESS_MAX_ANSWER", "0.25") or 0.25)
FAKE_USER = "00000000-0000-0000-0000-00000000beef"
WRITE_TOOLS = ("remember", "forget", "record_trade_response", "log_prediction")

# how many of each kind of question, out of QUESTIONS
MIX = {"start_sit": 15, "win": 5, "pickup": 8, "trade_find": 6, "trade_target": 6, "buy_sell": 3, "playoffs": 5, "partner": 2}


def _pos(p: Mapping[str, Any]) -> str:
    return str(p.get("pos") or "").upper()


def candidates(mine: Mapping[str, Any], rankings: list[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Questions this owner could be asked, best first, by kind."""
    lineup = [dict(p) for p in mine.get("week_lineup") or []]
    bench = [dict(p) for p in mine.get("week_bench_detail") or [] if (p.get("pts") or 0) > 0]
    weak = str(mine.get("weakest_slot") or "RB")
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in MIX}

    pairs = []
    for b in bench:
        for s in lineup:
            if _pos(s) == _pos(b) and s.get("id") != b.get("id"):
                pairs.append((abs(float(s.get("pts") or 0) - float(b.get("pts") or 0)), s, b))
    seen: set[str] = set()
    for _, s, b in sorted(pairs, key=lambda x: x[0]):
        if s["id"] in seen or b["id"] in seen:
            continue
        seen |= {s["id"], b["id"]}
        a, c = (s, b) if len(seen) % 4 else (b, s)  # don't always name the app's starter first
        out["start_sit"].append({"q": f"Should I start {a['name']} or {c['name']} this week?",
                                 "players": [{"id": s["id"], "name": s["name"], "app_pts": s.get("pts"), "app_starter": True},
                                             {"id": b["id"], "name": b["name"], "app_pts": b.get("pts"), "app_starter": False}]})

    out["win"].append({"q": "What are my chances of winning this week, and is there any lineup change that gives me a better shot?"})
    out["pickup"].append({"q": f"Who is the best free agent I could pick up to help at {weak}, who would I drop, and how much FAAB should I bid?"})
    out["trade_find"].append({"q": f"Find me a trade that upgrades my {weak} without hurting my starting lineup. What should I offer first?"})

    wpos = weak.upper().replace("FLEX", "RB")
    targets = []
    for r in rankings:
        if r.get("team_id") == mine.get("team_id"):
            continue
        for p in r.get("week_lineup") or []:
            if _pos(p) == wpos:
                targets.append((float(p.get("pts") or 0), p, r))
    for _, p, r in sorted(targets, key=lambda x: -x[0])[:2]:
        out["trade_target"].append({"q": f"What would it take to get {p['name']} from {r['owner']}? Give me an opening offer and what fair value is.",
                                    "players": [{"id": p["id"], "name": p["name"]}], "partner": r["owner"]})

    top = sorted(lineup, key=lambda p: -float(p.get("pts") or 0))
    if top:
        p = top[min(1, len(top) - 1)]
        out["buy_sell"].append({"q": f"Should I sell {p['name']} now or hold him? Is he worth more to me or on the trade market?",
                                "players": [{"id": p["id"], "name": p["name"]}]})
    out["playoffs"].append({"q": "What are my playoff odds right now, and what do I need to do the rest of the season to make the playoffs?"})
    out["partner"].append({"q": "Which owner in the league is most likely to actually make a trade with me this season, and what would they want from my roster?"})
    return out


def plan(per_team: list[tuple[Any, dict[str, list[dict[str, Any]]]]], total: int) -> list[tuple[Any, str, dict[str, Any]]]:
    """Spread each kind's quota across owners round robin, then group by owner (so the owner's pack stays cached)."""
    scale = total / sum(MIX.values())
    picked: dict[int, list[tuple[str, dict[str, Any]]]] = {i: [] for i in range(len(per_team))}
    start = 0
    for kind, n in MIX.items():
        want = max(1, round(n * scale))
        depth = [0] * len(per_team)
        i, tries = start, 0
        while want and tries < len(per_team) * 4:
            cands = per_team[i % len(per_team)][1].get(kind) or []
            k = i % len(per_team)
            if depth[k] < len(cands):
                picked[k].append((kind, cands[depth[k]]))
                depth[k] += 1
                want -= 1
            i += 1
            tries += 1
        start += 3  # next kind starts with a different owner
    out = []
    for k, items in picked.items():
        for kind, q in items:
            out.append((per_team[k][0], kind, q))
    return out[:total]


class Recorder:
    """Wraps every tool: keeps args and a trimmed copy of each result; write tools only record."""

    def __init__(self, tools: Any, resolve: Any):
        self.calls: list[dict[str, Any]] = []
        self.logged: list[dict[str, Any]] = []
        self.resolve = resolve
        for name, spec in tools.tools.items():
            spec.fn = self._wrap(name, spec.fn)

    def _wrap(self, name: str, inner: Any) -> Any:
        def run(**kw: Any) -> Any:
            t0 = time.monotonic()
            if name in WRITE_TOOLS:
                if name == "log_prediction":
                    self.logged.append({**kw, "pick_ids": self._ids(kw.get("pick")), "over_ids": self._ids(kw.get("over"))})
                out: Any = {"logged": True, "note": "saved"}
            else:
                try:
                    out = inner(**kw)
                except Exception as exc:
                    out = {"error": f"{name} failed: {type(exc).__name__}: {str(exc)[:300]}"}
            text = out if isinstance(out, str) else json.dumps(out, default=str)
            self.calls.append({"tool": name, "args": kw, "ms": int(1000 * (time.monotonic() - t0)), "chars": len(text), "result": text[:6000]})
            return out
        return run

    def _ids(self, names: Any) -> list[dict[str, Any]]:
        out = []
        for raw in names or []:
            name = str(raw.get("name") if isinstance(raw, Mapping) else raw or "")
            try:
                row = self.resolve(name) or {}
            except Exception:
                row = {}
            out.append({"name": row.get("full_name") or name, "sleeper_id": str(row.get("sleeper_id") or "") or None, "position": row.get("position")})
        return out


def _owners(client: Any, league_id: str, season: int) -> list[Any]:
    from league_ai import runtime

    teams = client.table("league_teams").select("*").eq("league_id", league_id).execute().data or []
    try:
        members = client.table("league_memberships").select("*").eq("league_id", league_id).execute().data or []
    except Exception:
        members = []
    by_team = {str(m.get("league_team_id") or m.get("team_id")): m for m in members}
    out = []
    for t in teams:
        if t.get("sleeper_roster_id") is None:
            continue
        m = by_team.get(str(t["id"])) or {}
        name = t.get("owner_name") or m.get("owner_name") or "Owner"
        # owners without an app account get their own stand-in id: the app caches each owner's pack and
        # Front Office data by user id, so a shared stand-in would answer everyone with one owner's roster
        stand_in = str(uuid.uuid5(uuid.UUID(FAKE_USER), str(t["id"])))
        out.append(runtime.Owner(user_id=str(m.get("user_id") or stand_in), league_id=league_id, league_team_id=str(t["id"]), season=season,
                                 owner_name=name, team_name=t.get("team_name") or name, role="owner"))
    return out


def league_state(client: Any, league_id: str, season: int) -> dict[str, Any]:
    """The canonical team state through read_canonical_team_state_for_jobs (service key only), with the app's normalisation."""
    from services.team_roster_state import load_team_state

    class Jobs:
        def rpc(self, name: str, params: Mapping[str, Any]) -> Any:
            req = params["p_request"]
            return client.rpc("read_canonical_team_state_for_jobs", {"p_league_id": req["league_id"], "p_season": int(req["season"])})

    return load_team_state(Jobs(), league_id, season)


def ask(client: Any) -> int:
    import requests

    from api import front_office as fo_api
    from league_ai import runtime
    from league_ai.config import load_config
    from league_ai.front_office import week_context
    from league_ai.routing import effort_for, topic
    from league_ai.service import answer
    from league_ai.tools.nfl import NflData
    from league_ai.usage import cost_usd

    league = next(iter(client.table("leagues").select("id, name, sleeper_league_id").not_.is_("sleeper_league_id", "null").limit(1).execute().data or []), None)
    if not league:
        print("no league with a Sleeper id")
        return 1
    state_nfl = requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15).json()
    season, sid = int(os.environ.get("STRESS_SEASON") or state_nfl.get("season")), str(league["sleeper_league_id"])
    config = load_config()
    print(f"League {league['name']} season {season} week {state_nfl.get('week')} model {config.model} budget ${BUDGET:.2f}", flush=True)

    sessions, per_team = {}, []
    shared = league_state(client, str(league["id"]), season)
    for o in _owners(client, str(league["id"]), season):
        try:
            s = fo_api.Session(client, o, sid)
            s.state = lambda: shared
            fo = s.front_office()
            mine = next((r for r in fo.get("rankings") or [] if str(r.get("team_id")) == o.league_team_id), None)
            if not mine:
                continue
            sessions[o.league_team_id] = (s, fo)
            per_team.append((o, candidates(mine, fo.get("rankings") or [])))
        except Exception as exc:
            print(f"skip {o.owner_name}: {type(exc).__name__}: {exc}", flush=True)
    queue = plan(per_team, QUESTIONS)
    print(f"{len(per_team)} owners, {len(queue)} questions planned", flush=True)

    results: dict[str, Any] = {"league": league["name"], "season": season, "week": int(state_nfl.get("week") or 0), "model": config.model,
                               "budget": BUDGET, "items": [], "spent": 0.0, "stopped_early": False}
    spent = 0.0
    for n, (o, kind, q) in enumerate(queue, 1):
        if spent + MAX_ANSWER > BUDGET:
            results["stopped_early"] = True
            print(f"budget stop before question {n}: spent ${spent:.2f}", flush=True)
            break
        s, fo = sessions[o.league_team_id]
        state = s.state()
        try:
            from api import league as league_api
            names = league_api.roster_names(client, o.league_id, sid)
            state = {**state, "sleeper_rostered": {pid: names.get(rid, f"Roster {rid}") for pid, rid in league_api.sleeper_owners(sid).items()}}
        except Exception:
            pass
        tools = runtime.chat_tools(client, o, state=state, rules=s.rules(), league=s.league, week=s.week(), positions=s.positions,
                                   playoff_week=s.playoff_start, question=lambda: q["q"], conversation_id=lambda: None,
                                   odds=lambda: fo_api._gm_odds(s, fo), extra=[fo_api.matchup_tool(s, fo)])
        nfl = NflData(client, league_state=state)
        rec = Recorder(tools, lambda name: next(iter(nfl.find_players(name, limit=1)), None))
        t0 = time.monotonic()
        try:
            res = answer(question=q["q"], history=[], asker=o.asker(), sources=runtime.LeagueDataSources(client=client, league_id=o.league_id, season=o.season),
                         tools=tools, client=runtime.ClaudeClient(), pack=s.pack(), week_context=week_context(fo, o.owner_name), turn_effort=effort_for(q["q"]))
            tr = res.trace.as_dict()
            cost = cost_usd(tr.get("model") or config.model, input_tokens=tr["input_tokens"], output_tokens=tr["output_tokens"],
                            cache_read_tokens=tr["cache_read_tokens"], cache_write_tokens=tr["cache_creation_tokens"]) or 0.0
            item = {"n": n, "kind": kind, "owner": o.owner_name, "question": q["q"], "meta": {k: v for k, v in q.items() if k != "q"},
                    "ok": res.ok, "error": res.error_code, "answer": res.text, "trace": tr, "cost": round(cost, 4), "pack_chars": res.pack_chars,
                    "seconds": round(time.monotonic() - t0, 1), "tool_calls": rec.calls, "logged": rec.logged, "topic": topic(q["q"]),
                    "effort": effort_for(q["q"])}
        except Exception as exc:
            cost = 0.0
            item = {"n": n, "kind": kind, "owner": o.owner_name, "question": q["q"], "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "tool_calls": rec.calls, "cost": 0.0}
        spent += cost
        results["items"].append(item)
        results["spent"] = round(spent, 4)
        print(f"{n:>2}. {kind:<12} {o.owner_name:<14} ok={item['ok']} ${cost:.3f} total ${spent:.2f} tools={len(rec.calls)} {item.get('seconds', 0)}s", flush=True)
        with open(RESULTS, "w") as f:
            json.dump(results, f, default=str)

    results["track_record"] = track_record(client, str(league["id"]), season)
    with open(RESULTS, "w") as f:
        json.dump(results, f, default=str)
    print(f"done: {len(results['items'])} answers, ${spent:.2f}", flush=True)
    return 0


def track_record(client: Any, league_id: str, season: int) -> dict[str, Any]:
    """The GM's real graded calls so far and its live lessons (read only)."""
    try:
        preds = (client.table("league_ai_predictions").select("kind, season, week, pick, over, verdict, correct, pick_points, over_points, cause, process_error, "
                 "diagnosis, lesson, status, confidence, factors").eq("league_id", league_id).limit(1000).execute().data or [])
    except Exception as exc:
        preds = [{"error": str(exc)[:200]}]
    try:
        lessons = client.table("league_ai_lessons").select("*").eq("league_id", league_id).limit(100).execute().data or []
    except Exception:
        lessons = []
    try:
        card = client.table("league_ai_scorecards").select("*").eq("league_id", league_id).eq("season", season).limit(1).execute().data or []
    except Exception:
        card = []
    return {"predictions": preds, "lessons": lessons, "scorecard": card}


def grade_saved(client: Any, path: str) -> int:
    """Score the calls saved by `ask` against real league points (weeks that aren't final yet are skipped)."""
    from league_ai.learning import grade
    from league_ai.learning_job import WeeklyStats, completed_week, league_scoring, league_points

    data = json.load(open(path))
    league = next(iter(client.table("leagues").select("id").not_.is_("sleeper_league_id", "null").limit(1).execute().data or []), None)
    scoring = league_scoring(client, str(league["id"])) if league else {}
    season, done, stats = int(data["season"]), completed_week(client, int(data["season"])), WeeklyStats()
    horizon = {"start_sit": 1, "pickup": 3, "trade": 4}
    out = []
    for item in data["items"]:
        for call in item.get("logged") or []:
            kind = str(call.get("kind") or "")
            pred = {"kind": kind, "week": int(call.get("week") or data["week"]), "horizon_weeks": horizon.get(kind, 1),
                    "pick": call.get("pick_ids") or [], "over": call.get("over_ids") or [], "verdict": call.get("verdict")}
            last = pred["week"] + pred["horizon_weeks"] - 1
            if last > done:
                out.append({"n": item["n"], "kind": kind, "status": f"waiting for week {last}"})
                continue
            g = grade(pred, lambda s, w: league_points(stats.week(season, w).get(str(s)), scoring))
            out.append({"n": item["n"], "kind": kind, "confidence": call.get("confidence"), "pick": [p["name"] for p in pred["pick"]],
                        "over": [p["name"] for p in pred["over"]], **({k: g[k] for k in ("pick_points", "over_points", "correct", "margin")} if g else {"status": "ungradeable"})})
        meta = item.get("meta") or {}
        if item["kind"] == "start_sit" and meta.get("players") and data["week"] <= done:
            pts = {p["id"]: league_points(stats.week(season, data["week"]).get(str(p["id"])), scoring) for p in meta["players"]}
            out.append({"n": item["n"], "kind": "app_lineup", "players": [{**p, "actual": pts[p["id"]]} for p in meta["players"]]})
    json.dump(out, open("stress_grades.json", "w"), default=str)
    for row in out:
        print(json.dumps(row, default=str))
    return 0


def rosters(client: Any) -> int:
    """Free check: players per team in the app's league state vs on Sleeper (counts only, safe for a public log)."""
    import requests

    from services.team_roster_state import state_roster

    league = next(iter(client.table("leagues").select("id, sleeper_league_id").not_.is_("sleeper_league_id", "null").limit(1).execute().data or []))
    season = int(os.environ.get("STRESS_SEASON") or requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15).json()["season"])
    state = league_state(client, str(league["id"]), season)
    roster = state_roster(state)
    teams = client.table("league_teams").select("id, owner_name, sleeper_roster_id").eq("league_id", league["id"]).execute().data or []
    sleeper = {r["roster_id"]: r for r in requests.get(f"https://api.sleeper.app/v1/league/{league['sleeper_league_id']}/rosters", timeout=15).json()}
    print(f"state season {state.get('season')} roster rows {len(roster)} keys {sorted(state.keys())}")
    for t in teams:
        mine = [r for r in roster if str(r.get("league_team_id")) == str(t["id"])]
        ids = {str(r.get("sleeper_id")) for r in mine}
        sr = sleeper.get(t.get("sleeper_roster_id")) or {}
        sl = {str(p) for k in ("players", "taxi", "reserve") for p in (sr.get(k) or [])}
        print(f"{t['owner_name']:<16} app {len(mine):>2}  sleeper {len(sl):>2}  on Sleeper but not in app {len(sl - ids):>2}  in app not on Sleeper {len(ids - sl):>2}")
    return 0


def main() -> int:
    from supabase import create_client

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    mode = sys.argv[1] if len(sys.argv) > 1 else "ask"
    if mode == "rosters":
        return rosters(client)
    if mode == "grade":
        return grade_saved(client, sys.argv[2] if len(sys.argv) > 2 else "stress/grading-input.json")
    return ask(client)


if __name__ == "__main__":
    raise SystemExit(main())
