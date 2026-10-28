"""Practice test before every deploy: the engine's own numbers on the real league, no model calls (free).

The Ship app workflow runs this first and stops the deploy when it fails. It checks:
1. Start/sit: on every saved lineup, the engine's pick (projection + matchup) is right at least as often as the owners
   were, and above START_SIT_FLOOR.
2. Playoff odds: the 2-season backtest Brier score stays under the limits (weeks 1-5 and week 6 on).
3. Trade builder and roster tools for every owner: find_trade_targets returns cap-legal ladders, a named-player
   price comes back (a ladder or "nothing fair" with the closest tries), plan_roster_moves and get_playoff_odds answer.
Prints counts and rates only (the repo and its Actions logs are public).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Mapping

START_SIT_FLOOR = 0.66        # 2024-26: engine 69.9% of 5,940 calls, owners 68.6%
START_SIT_SLACK = 0.005       # the engine may trail the owners by half a point before the test fails
ODDS_EARLY_MAX = 0.22         # 2024-25 backtest weeks 1-5: 0.196 after the early-season fix
ODDS_LATE_MAX = 0.09          # week 6 on: 0.070
STAND_IN = "00000000-0000-0000-0000-00000000beef"


def check_start_sit(client: Any, league: Mapping[str, Any]) -> list[str]:
    from league_ai.start_sit_grade import run_league

    res = run_league(client, str(league["id"]), str(league["sleeper_league_id"]))
    allc = res.get("all") or {}
    if not allc:
        return ["start/sit: no graded lineups"]
    eng, own = allc["projection_matchup"], allc["owner"]
    print(f"start/sit: engine {eng['right']:.1%} vs owners {own['right']:.1%} over {eng['calls']} calls; close calls "
          f"{(res.get('close') or {}).get('projection_matchup', {}).get('right', 0):.1%}; when they disagree {res['engine_vs_owner_when_they_disagree']}", flush=True)
    fails = []
    if eng["right"] < START_SIT_FLOOR:
        fails.append(f"start/sit right {eng['right']:.1%} is under the {START_SIT_FLOOR:.0%} floor")
    if eng["right"] < own["right"] - START_SIT_SLACK:
        fails.append(f"start/sit engine ({eng['right']:.1%}) trails the owners ({own['right']:.1%})")
    return fails


def check_odds(client: Any, league: Mapping[str, Any]) -> list[str]:
    from league_ai.playoff_odds import backtest_season, score_backtest

    rows = (client.table("league_matchups").select("season, week, owner_name, opponent_name, points, standing_points, is_playoff")
            .eq("league_id", league["id"]).limit(20000).execute().data or [])
    seasons = sorted({int(r["season"]) for r in rows})
    done = [s for s in seasons if any(r.get("is_playoff") for r in rows if int(r["season"]) == s)]
    preds = [p for s in done for p in backtest_season([r for r in rows if int(r["season"]) == s], sims=1000)]
    if not preds:
        print("playoff odds: no finished season to backtest", flush=True)
        return []
    early = score_backtest([p for p in preds if p["week"] <= 5]).get("brier")
    late = score_backtest([p for p in preds if p["week"] > 5]).get("brier")
    print(f"playoff odds: {len(preds)} predictions over {len(done)} seasons, Brier weeks 1-5 {early}, week 6 on {late}", flush=True)
    fails = []
    if early is not None and early > ODDS_EARLY_MAX:
        fails.append(f"early-season odds Brier {early} over {ODDS_EARLY_MAX}")
    if late is not None and late > ODDS_LATE_MAX:
        fails.append(f"late-season odds Brier {late} over {ODDS_LATE_MAX}")
    return fails


def _state(client: Any, league_id: str, season: int) -> dict[str, Any]:
    """The canonical team state through the jobs RPC (service key), as the app sees it."""
    from services.team_roster_state import load_team_state

    class Jobs:
        def rpc(self, name: str, params: Mapping[str, Any]) -> Any:
            req = params["p_request"]
            return client.rpc("read_canonical_team_state_for_jobs", {"p_league_id": req["league_id"], "p_season": int(req["season"])})

    return load_team_state(Jobs(), league_id, season)


def _call(tools: Any, name: str, **kw: Any) -> dict[str, Any] | None:
    spec = tools.tools.get(name)
    if spec is None:
        return None
    try:
        out = json.loads(tools.call(name, kw))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}"}
    return out if isinstance(out, dict) else {"result": out}


def check_tools(client: Any, league: Mapping[str, Any], season: int) -> list[str]:
    from api import front_office as fo_api
    from league_ai import runtime
    from league_ai.sleeper_roster import fill_from_sleeper

    lid, sid = str(league["id"]), str(league["sleeper_league_id"])
    shared = fill_from_sleeper(client, _state(client, lid, season), sid, lid)
    teams = client.table("league_teams").select("id, owner_name, team_name, sleeper_roster_id").eq("league_id", lid).execute().data or []
    counts = {"owners": 0, "idea_lists": 0, "ideas": 0, "ladders": 0, "targets_priced": 0, "roster_plans": 0, "odds": 0}
    fails: list[str] = []
    for n, t in enumerate([t for t in teams if t.get("sleeper_roster_id") is not None], start=1):
        o = runtime.Owner(user_id=str(uuid.uuid5(uuid.UUID(STAND_IN), str(t["id"]))), league_id=lid, league_team_id=str(t["id"]), season=season,
                          owner_name=t.get("owner_name") or "Owner", team_name=t.get("team_name") or "Team", role="owner")
        try:
            s = fo_api.Session(client, o, sid)
            s.state = lambda: shared
            fo = s.front_office()
            tools = runtime.chat_tools(client, o, state=shared, rules=s.rules(), league=s.league, week=s.week(), positions=s.positions,
                                       playoff_week=s.playoff_start, question=lambda: "practice test", conversation_id=lambda: None,
                                       odds=lambda: fo_api._gm_odds(s, fo))
        except Exception as exc:
            fails.append(f"team {n}: tools didn't load ({type(exc).__name__})")
            continue
        finder = tools.tools.get("find_trade_targets")
        counts["owners"] += 1
        ideas = _call(tools, "find_trade_targets") or {}
        if "error" in ideas:
            fails.append(f"team {n}: find_trade_targets error")
        else:
            counts["idea_lists"] += 1
            for idea in ideas.get("ideas") or []:
                counts["ideas"] += 1
                if idea.get("open_with"):
                    counts["ladders"] += 1
                cap = idea.get("cap_after") or {}
                if min(cap.get("you", 0), cap.get("them", 0)) < 0:
                    fails.append(f"team {n}: an idea breaks the cap")
                if str(idea.get("partner")) == str(o.owner_name):
                    fails.append(f"team {n}: an idea trades with itself")
            first = next(iter(ideas.get("ideas") or []), None)
            if first and finder is not None:
                target = str(first["you_get"][0]).split(" (")[0]
                priced = _call(tools, "find_trade_targets", target=target) or {}
                if "error" in priced or ("open_with" not in priced and not priced.get("closest_tries") and priced.get("fair_deal_found") is not False):
                    fails.append(f"team {n}: a named-player price came back empty")
                else:
                    counts["targets_priced"] += 1
        plan = _call(tools, "plan_roster_moves")
        if plan is not None:
            if "error" in plan:
                fails.append(f"team {n}: plan_roster_moves error")
            else:
                counts["roster_plans"] += 1
        odds = _call(tools, "get_playoff_odds")
        if odds is not None and "error" not in odds and odds.get("odds"):
            total = sum(odds["odds"].values())
            spots = int(odds.get("spots") or 4)
            if abs(total - 100 * spots) > 5 * len(odds["odds"]):
                fails.append(f"team {n}: playoff odds add up to {total}%, expected about {100 * spots}%")
            counts["odds"] += 1
    print(f"tools: {counts}", flush=True)
    if counts["owners"] and counts["idea_lists"] < counts["owners"]:
        fails.append(f"trade finder answered for {counts['idea_lists']} of {counts['owners']} owners")
    return fails


def main() -> int:
    import requests
    from supabase import create_client

    os.environ["LEAGUE_AI_IDEA_LOG"] = "off"   # practice answers must not count as ideas owners saw
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    seasons = client.table("league_seasons").select("league_id, season").eq("is_active", True).order("season", desc=True).limit(1).execute().data or []
    if not seasons:
        print("no active league season; nothing to test")
        return 0
    league = next(iter(client.table("leagues").select("id, sleeper_league_id").eq("id", seasons[0]["league_id"]).limit(1).execute().data or []), None)
    if not league or not league.get("sleeper_league_id"):
        print("active league has no Sleeper id; nothing to test")
        return 0
    season = int(requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15).json().get("season") or seasons[0]["season"])
    fails: list[str] = []
    for name, check in (("start/sit", lambda: check_start_sit(client, league)), ("odds", lambda: check_odds(client, league)),
                        ("tools", lambda: check_tools(client, league, season))):
        try:
            fails += check()
        except Exception as exc:
            fails.append(f"{name} check crashed: {type(exc).__name__}: {str(exc)[:200]}")
    if fails:
        print("PRACTICE TEST FAILED:\n- " + "\n- ".join(fails), flush=True)
        return 1
    print("practice test passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
