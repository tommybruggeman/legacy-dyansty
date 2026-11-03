"""Run plan_future for every owner on the live league, no model calls (free).

Prints aggregate numbers only (the repo and its Actions logs are public). The full answers are written gzipped to
FUTURE_OUT for the workflow to encrypt with league_ai/checks_public_key.pem before anything is printed.
Run: FUTURE_OUT=/tmp/future.json.gz python -m league_ai.future_check
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import uuid
from statistics import median


def main() -> int:
    import requests
    from supabase import create_client

    from api import front_office as fo_api
    from league_ai import runtime
    from league_ai.practice_test import STAND_IN, _call, _state, future_problems
    from league_ai.sleeper_roster import fill_from_sleeper

    os.environ["LEAGUE_AI_IDEA_LOG"] = "off"
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    seasons = client.table("league_seasons").select("league_id, season").eq("is_active", True).order("season", desc=True).limit(1).execute().data or []
    league = next(iter(client.table("leagues").select("id, sleeper_league_id").eq("id", seasons[0]["league_id"]).limit(1).execute().data or []), None)
    season = int(requests.get("https://api.sleeper.app/v1/state/nfl", timeout=15).json().get("season") or seasons[0]["season"])
    lid, sid = str(league["id"]), str(league["sleeper_league_id"])
    shared = fill_from_sleeper(client, _state(client, lid, season), sid, lid)
    teams = client.table("league_teams").select("id, owner_name, team_name, sleeper_roster_id").eq("league_id", lid).execute().data or []
    out, problems, rooms, heats, ratios, counts = {}, [], [], [], [], []
    for n, t in enumerate([t for t in teams if t.get("sleeper_roster_id") is not None], start=1):
        o = runtime.Owner(user_id=str(uuid.uuid5(uuid.UUID(STAND_IN), str(t["id"]))), league_id=lid, league_team_id=str(t["id"]), season=season,
                          owner_name=t.get("owner_name") or "Owner", team_name=t.get("team_name") or "Team", role="owner")
        s = fo_api.Session(client, o, sid)
        s.state = lambda: shared
        tools = runtime.chat_tools(client, o, state=shared, rules=s.rules(), league=s.league, week=s.week(), positions=s.positions,
                                   playoff_week=s.playoff_start, question=lambda: "future check", conversation_id=lambda: None)
        plan = _call(tools, "plan_future") or {"error": "no tool"}
        problems += [f"team {n}: {p}" for p in future_problems(plan, float(s.rules().get("salary_cap") or 225))]
        out[o.owner_name] = plan
        if "error" in plan:
            continue
        rooms.append(float(plan["seasons"][0]["room"]))
        first = plan["free_agent_classes"][0]
        if isinstance(first.get("market_heat"), (int, float)):
            heats.append(first["market_heat"])
        counts.append(len(first.get("targets") or []))
        ratios += [t["likely_price"] / t["worth_then"] for t in first.get("targets") or [] if t.get("worth_then")]
    print(f"plans: {len(out)}, problems: {len(problems)}", flush=True)
    if rooms:
        print(f"next-season room: median ${median(rooms):.0f}, min ${min(rooms):.0f}, max ${max(rooms):.0f}", flush=True)
    if heats:
        print(f"next auction market heat: {heats[0]}", flush=True)
    if counts:
        print(f"targets in the next class per owner: median {median(counts)}; price/worth median {median(ratios):.2f}" if ratios else "", flush=True)
    for p in problems[:10]:
        print(f"- {p}", flush=True)
    path = os.environ.get("FUTURE_OUT")
    if path:
        with gzip.open(path, "wt") as f:
            json.dump(out, f, default=str)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
