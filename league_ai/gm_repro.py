"""Replay one Assistant GM chat the way the app sends it, including messages typed while the GM is working.
Costs a little money (one or two answers). Prints only codes and the API's own error text (no league data).

    python -m league_ai.gm_repro
"""
from __future__ import annotations

import os
import uuid
from typing import Any

STAND_IN = "00000000-0000-0000-0000-00000000beef"
Q1 = "How many points was {name} projected before the game stated"
ADD1 = "Started"
ADD2 = "Same with Laurence"


def main() -> int:
    from supabase import create_client

    from api import front_office as fo_api
    from league_ai import client as client_mod, runtime
    from league_ai.sleeper_roster import fill_from_sleeper
    from league_ai.answer_eval import _quiet, _state
    from league_ai.config import load_config
    from league_ai.front_office import week_context
    from league_ai.routing import effort_for
    from league_ai.service import LeagueDataSources, answer

    os.environ["LEAGUE_AI_IDEA_LOG"] = "off"
    os.environ["LEAGUE_AI_FACT_CHECK"] = os.environ.get("LEAGUE_AI_FACT_CHECK", "off")
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    seasons = client.table("league_seasons").select("league_id, season").eq("is_active", True).order("season", desc=True).limit(1).execute().data or []
    lid, season = str(seasons[0]["league_id"]), int(seasons[0]["season"])
    league = next(iter(client.table("leagues").select("id, sleeper_league_id").eq("id", lid).limit(1).execute().data or []), {})
    sid = str(league.get("sleeper_league_id") or "")
    shared = fill_from_sleeper(client, _state(client, lid, season), sid, lid) if sid else _state(client, lid, season)
    teams = client.table("league_teams").select("id, owner_name, team_name, sleeper_roster_id").eq("league_id", lid).execute().data or []
    t = next(t for t in teams if t.get("sleeper_roster_id") is not None)
    o = runtime.Owner(user_id=str(uuid.uuid5(uuid.UUID(STAND_IN), str(t["id"]))), league_id=lid, league_team_id=str(t["id"]), season=season,
                      owner_name=t.get("owner_name") or "Owner", team_name=t.get("team_name") or "Team")
    s = fo_api.Session(client, o, sid)
    s.state = lambda: shared

    # print the API's own reason for any rejected request (it names the bad field, never league data)
    real_call = client_mod.ClaudeClient._call

    def call(self: Any, sdk: Any, kwargs: dict[str, Any], on_event: Any) -> Any:
        try:
            return real_call(self, sdk, kwargs, on_event)
        except Exception as exc:
            roles = [m.get("role") for m in kwargs.get("messages") or []]
            print(f"REJECTED {type(exc).__name__}: {str(getattr(exc, 'message', exc))[:600]} roles={roles}", flush=True)
            raise
    client_mod.ClaudeClient._call = call

    for variant in ("during_lookups", "after_answer"):
        sent = {"n": 0}

        def inbox(final: bool, _v=variant) -> list[str]:
            sent["n"] += 1
            if _v == "during_lookups" and not final and sent["n"] == 1:
                return [ADD1, ADD2]
            if _v == "after_answer":
                if not final and sent["n"] == 1:
                    return [ADD1]
                if final and sent["n"] <= 3:
                    sent["n"] = 99
                    return [ADD2]
            return []

        events: list[str] = []
        q = Q1.format(name="Bhayshul Tuten")
        fo = s.front_office()
        tools, _seen = fo_api.gm_tools(s, fo, question=lambda: q, conversation_id=lambda: None)
        _quiet(tools)  # nothing is written to any owner's notes or records
        res = answer(question=q, history=[], asker=o.asker(), sources=LeagueDataSources(client=client, league_id=lid, season=season), tools=tools,
                     client=runtime.ClaudeClient(load_config()), pack=s.pack(), on_event=lambda k, p: events.append(k),
                     week_context=week_context(fo, o.owner_name), turn_effort=effort_for(q), inbox=inbox)
        tr = res.trace
        print(f"VARIANT {variant}: ok={res.ok} error={res.error_code} rounds={tr.rounds} tools={tr.tool_calls} effort={tr.effort} "
              f"in={tr.input_tokens} out={tr.output_tokens} chars={len(res.text or '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
