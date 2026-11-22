from datetime import datetime, timezone

from api import usage_log, usage_stats

NOW = datetime(2026, 11, 23, 20, 0, tzinfo=timezone.utc)  # a Monday, 1 PM Mountain
OWNERS = {"a": {"name": "Ann", "team": "Aces"}, "b": {"name": "Bo", "team": "Bears"}, "c": {"name": "Cy", "team": "Cubs"}}


def r(user, at, screen, seconds=0, kind="screen", source="app", detail=None, build="112"):
    return {"user_id": user, "viewed_at": at, "screen": screen, "seconds": seconds, "kind": kind, "source": source, "detail": detail, "build": build}


ROWS = [
    r("a", "2026-11-23T19:00:00Z", "/", kind="app_open", detail={"cold": True, "os": "ios 26.1", "tz": "America/Denver"}),
    r("a", "2026-11-23T19:00:00Z", "/", 20),
    r("a", "2026-11-23T19:00:20Z", "/my-team", 60),
    r("a", "2026-11-23T19:00:30Z", "/my-team", kind="tap", detail={"x": 0.5, "y": 0.4}),
    r("a", "2026-11-23T19:00:40Z", "/my-team/lineup", kind="action"),
    r("a", "2026-11-23T19:01:20Z", "/player/[id]", 30),
    # second session for Ann, two hours later
    r("a", "2026-11-23T21:30:00Z", "/front-office", 90),
    # Bo on the website: page times come from the gap to the next page
    r("b", "2026-11-23T15:00:00Z", "web/02_My_Team", source="website", build=None),
    r("b", "2026-11-23T15:02:00Z", "web/03_Teams", source="website", build=None),
]


def test_sessions_split_on_gap_and_fill_web_time():
    s = usage_stats.sessions(ROWS)
    assert len(s) == 3
    web = [x for x in s if x["source"] == "website"][0]
    assert web["events"][0]["secs"] == 120


def test_build_numbers():
    out = usage_stats.build(ROWS, owners=OWNERS, ai_rows=[{"user_id": "a", "feature": "chat", "topic": "trade", "cost_usd": 0.1}],
                            days=7, now=NOW)
    t = out["totals"]
    assert t["sessions"] == 3 and t["actions"] == 1 and t["taps"] == 1 and t["gm_questions"] == 1
    assert out["actions"][0]["name"] == "Set a lineup"
    assert "My Team → Set a lineup" in [p["path"] for p in out["paths"]["pairs"]]
    assert out["first_moves"][0]["name"] in ("My Team", "Web: My Team")
    assert "Cy" in out["quiet"]
    ann = next(o for o in out["owners"] if o["name"] == "Ann")
    assert ann["sessions"] == 2 and ann["gm_questions"] == 1 and ann["device"]["os"] == "ios 26.1" and ann["build"] == "112"
    assert out["heatmap"][0][12] > 0  # Monday noon Mountain
    assert "League rules" in out["unused"]["screens"]
    assert out["taps"][0]["name"] == "My Team" and out["taps"][0]["spots"] == [[0.5, 0.4]]
    timeline = out["sessions"][0]
    assert timeline["owner"] == "Ann" and timeline["steps"][0]["name"] == "Front Office (GM)"


def test_one_owner_timeline():
    out = usage_stats.build(ROWS, owners=OWNERS, days=7, now=NOW, owner="b")
    assert {s["owner"] for s in out["sessions"]} == {"Bo"}


def test_action_names():
    assert usage_log.action_name("POST", "/trade-offers/0b7c1f2e-1111-2222-3333-444455556666/accept") == "/trade-offers/[id]/accept"
    assert usage_log.action_name("DELETE", "/my-team/ir") == "DELETE /my-team/ir"
    assert usage_log.action_name("GET", "/my-team") is None
    assert usage_log.action_name("POST", "/me/activity") is None


def test_rows_keep_kind_and_small_detail():
    rows = usage_log.rows(user_id="u", league_id="l", build="112", source="app",
                          views=[{"screen": "/my-team", "seconds": 0, "kind": "tap", "detail": {"x": 0.1, "y": 0.2}},
                                 {"screen": "/", "seconds": 0, "kind": "nope"}, {"screen": "/", "seconds": 5}])
    assert len(rows) == 2 and rows[0]["kind"] == "tap" and rows[0]["detail"] == {"x": 0.1, "y": 0.2} and "kind" not in rows[1]


def test_periods_by_week():
    out = usage_stats.build(ROWS, owners=OWNERS, days=7, now=NOW, by="week")
    assert out["by"] == "week" and len(out["periods"]) == 1
    p = out["periods"][0]
    assert p["period"] == "2026-W48" and p["owners"] == 2 and p["sessions"] == 3
    assert {"name": "My Team", "count": 1} in p["visits"]


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _FakeSDK:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return _Block(content=[_Block(type="tool_use", id="t1", name="count_events", input={"group_by": ["owner"], "kind": "screen"})], usage=None)
        last = kw["messages"][-1]["content"][0]["content"]
        assert "Ann" in last
        return _Block(content=[_Block(type="text", text="Ann uses it most.")], usage=None)


def test_analyst_runs_tools():
    from api import usage_ai
    out = usage_ai.ask("Who uses it most?", rows=ROWS, owners=OWNERS, ai_rows=[], days=7, tz=None, sdk=_FakeSDK())
    assert out["answer"] == "Ann uses it most." and out["tools"] == ["count_events"]


def test_successful_posts_are_logged_as_actions(monkeypatch):
    import time
    from types import SimpleNamespace

    from fastapi import Depends, Request
    from fastapi.testclient import TestClient

    from api import main

    saved = []
    monkeypatch.setattr(main.usage_log, "save_action", lambda client, **kw: saved.append(kw))
    monkeypatch.setattr(main, "resolve_team", lambda client, user: {"league_id": "L"})

    def fake_owner(request: Request):
        request.state.owner = (object(), SimpleNamespace(id="u1", email="o@example.com"))
        return request.state.owner

    @main.app.post("/__test/lineup")
    def _route(owner=Depends(fake_owner)):
        return {"ok": True}

    try:
        TestClient(main.app).post("/__test/lineup")
        for _ in range(50):
            if saved:
                break
            time.sleep(0.02)
        assert saved and saved[0]["path"] == "/__test/lineup" and saved[0]["league_id"] == "L" and saved[0]["user_id"] == "u1"
    finally:
        main.app.router.routes[:] = [r for r in main.app.router.routes if getattr(r, "path", "") != "/__test/lineup"]
