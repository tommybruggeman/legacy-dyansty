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


# --- the console feed, reports and the analyst's report/session tools (api/usage_console.py)

def test_console_feed_is_compact_and_consistent():
    from api import usage_console
    f = usage_console.feed(ROWS, owners=OWNERS, ai_rows=[], days=7, tz="America/Denver")
    assert f["owners"][:3] == ["Ann", "Bo", "Cy"] and len(f["sessions"]) == 3
    kinds = f["kinds"]
    for o, t, k, l, secs, web, sess, build in f["events"]:
        assert f["sessions"][sess][0] == o and kinds[k] != "frame"
    lineup = [e for e in f["events"] if f["labels"][e[3]] == "Set a lineup"]
    assert len(lineup) == 1 and kinds[lineup[0][2]] == "action"
    first = f["sessions"][f["events"][0][6]]
    assert [f["labels"][i] for i in first[3]][:2] == ["Web: My Team", "Web: Teams"] or first[5].startswith("b|")


def test_report_runner_groups_splits_and_lists_sessions():
    from api import usage_console
    data = usage_console.Data(ROWS, owners=OWNERS, tz="America/Denver")
    out = usage_console.run_report(data, {"name": "Views", "metric": "Page views", "group": "Owner", "split": "Platform", "videos": True}, days=7, now=NOW)
    assert out["keys"] == ["Ann", "Bo"]
    by = {s["name"]: s["data"] for s in out["series"]}
    assert by["App"] == [4, 0] and by["Website"] == [0, 2]
    assert out["session_count"] == 3 and out["sessions"][0]["owner"] == "Ann"
    assert usage_console.clean_report({"metric": "Bogus", "group": "Owner", "name": "x" * 200, "kind": "nope"}) == {
        "name": "x" * 80, "metric": "Page views", "group": "Owner", "split": "None", "chart": "Bars", "kind": "", "page": "", "owner": "", "note": "",
        "days": 0, "videos": False}
    found = usage_console.find_sessions(data, action="lineup")
    assert found["matched"] == 1 and found["sessions"][0]["path"][:3] == ["Home", "My Team", "Set a lineup"]


def test_session_detail_and_signing():
    from types import SimpleNamespace

    from api import usage_console
    rows = ROWS + [r("a", "2026-11-23T19:00:31Z", "/my-team", kind="frame", detail={"path": "a/1/001.jpg"})]
    key = usage_console.find_sessions(usage_console.Data(rows, owners=OWNERS, tz=None), action="lineup")["sessions"][0]["key"]
    s = usage_console.session_detail(rows, owners=OWNERS, key=key, tz="America/Denver")
    assert s["owner"] == "Ann" and s["frames"][0]["path"] == "a/1/001.jpg" and any(x["kind"] == "tap" and x["detail"] for x in s["steps"])
    assert usage_console.session_detail(rows, owners=OWNERS, key="a|1", tz=None) is None
    seen = {}

    def post(url, json, headers, timeout):
        seen.update(url=url, auth=headers["Authorization"], paths=json["paths"])
        return SimpleNamespace(status_code=200, json=lambda: [{"path": "a/1/001.jpg", "signedURL": "/object/sign/replays/a/1/001.jpg?token=x"}])

    urls = usage_console.sign_frames("TOKEN", ["a/1/001.jpg"], post=post)
    assert seen["auth"] == "Bearer TOKEN" and seen["url"].endswith("/storage/v1/object/sign/replays")
    assert urls["a/1/001.jpg"].endswith("/storage/v1/object/sign/replays/a/1/001.jpg?token=x")


class _ReportSDK:
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        import json
        self.calls += 1
        if self.calls == 1:
            assert "Set a lineup" in kw["system"][1]["text"]  # pages and actions are in the up-front context
            return _Block(content=[_Block(type="tool_use", id="t1", name="find_sessions", input={"owner": "ann"})], usage=None)
        if self.calls == 2:
            keys = [s["key"] for s in json.loads(kw["messages"][-1]["content"][0]["content"])["sessions"]]
            return _Block(content=[_Block(type="tool_use", id="t2", name="build_report", input={"name": "Ann by day", "metric": "Page views", "group": "Day", "owner": "Ann"}),
                                   _Block(type="tool_use", id="t3", name="show_sessions", input={"keys": keys[:1], "title": "Ann's latest"})], usage=None)
        return _Block(content=[_Block(type="text", text="Here's Ann.")], usage=None)


def test_analyst_hands_back_report_and_sessions():
    from api import usage_ai
    out = usage_ai.ask("Show me Ann", rows=ROWS, owners=OWNERS, ai_rows=[], days=7, tz=None, sdk=_ReportSDK())
    assert out["report"]["name"] == "Ann by day" and out["report"]["owner"] == "Ann"
    assert len(out["sessions"]) == 1 and out["sessions"][0]["owner"] == "Ann" and out["sessions_title"] == "Ann's latest"


def test_analyst_tools_cover_the_console():
    from api import usage_ai
    a = usage_ai.Analyst(ROWS, owners=OWNERS, ai_rows=[], days=7, tz="America/Denver", now=NOW)
    pages = {p["page"]: p for p in a.run_tool("page_table", {})["pages"]}
    assert pages["My Team"]["views"] == 1 and pages["My Team"]["taps"] == 1
    funnel = a.run_tool("funnel", {"steps": ["Home", "My Team", "Set a lineup"]})["steps"]
    assert [s["sessions"] for s in funnel] == [1, 1, 1]
    assert a.run_tool("flows", {"after": "Set a lineup"})["after"]["next"][0]["name"] == "Player page"
    assert a.run_tool("owner_profile", {"owner": "bo"})["owner"] == "Bo"
    assert "Cy" in a.run_tool("retention", {})["quiet_7_days"]


def test_console_endpoints(monkeypatch):
    import gzip
    import json
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from api import main
    store = []

    class T:
        def __init__(self):
            self.f, self.op = [], None
        def select(self, *_): self.op = "select"; return self
        def eq(self, k, v): self.f.append((k, v)); return self
        def order(self, *_): return self
        def insert(self, row): self.op = ("insert", row); return self
        def update(self, row): self.op = ("update", row); return self
        def delete(self): self.op = "delete"; return self
        def execute(self):
            hit = [x for x in store if all(str(x.get(k)) == str(v) for k, v in self.f)]
            if self.op == "select":
                return SimpleNamespace(data=hit)
            if self.op == "delete":
                for x in hit:
                    store.remove(x)
                return SimpleNamespace(data=hit)
            kind, row = self.op
            if kind == "insert":
                store.append({"id": str(len(store) + 1), **row})
                return SimpleNamespace(data=[store[-1]])
            for x in hit:
                x.update(row)
            return SimpleNamespace(data=hit)

    client = SimpleNamespace(table=lambda name: T())
    main.app.dependency_overrides[main.owner_client] = lambda: (client, SimpleNamespace(id="v"))
    monkeypatch.setattr(main, "_activity_viewer", lambda c, u: {"league_id": "L"})
    monkeypatch.setattr(main, "_activity_data", lambda c, lid, days: (ROWS, [], OWNERS))
    try:
        t = TestClient(main.app)
        page = t.get("/console")
        assert page.status_code == 200 and "Owner activity" in page.text
        feed = t.get("/commissioner/activity/feed?days=7", headers={"Accept-Encoding": "identity"})
        assert feed.json()["owners"][:2] == ["Ann", "Bo"]
        saved = t.post("/commissioner/activity/reports", json={"spec": {"name": "Mine", "metric": "Taps", "group": "Owner", "evil": 1}}).json()
        assert saved["metric"] == "Taps" and "evil" not in saved
        t.post("/commissioner/activity/reports", json={"spec": {"name": "Mine", "metric": "Sessions", "group": "Day"}})
        listed = t.get("/commissioner/activity/reports").json()["reports"]
        assert len(listed) == 1 and listed[0]["metric"] == "Sessions" and store[0]["user_id"] == "v"
        t.delete(f"/commissioner/activity/reports/{listed[0]['id']}")
        assert t.get("/commissioner/activity/reports").json()["reports"] == []
    finally:
        main.app.dependency_overrides.pop(main.owner_client, None)
