from datetime import datetime, timedelta, timezone

from api import alerts
from api import player_status as ps

T0 = datetime(2026, 11, 20, 20, 0, tzinfo=timezone.utc)


def payload(*entries):
    return {"injuries": [{"abbreviation": "LAR", "injuries": [
        {"athlete": {"id": eid, "displayName": name, "position": {"abbreviation": pos}}, "status": status,
         "details": {"type": part, **({"fantasyStatus": {"abbreviation": fs}} if fs else {})}}
        for eid, name, pos, status, part, fs in entries]}]}


def test_labels():
    assert ps.label("Questionable") == "Questionable"
    assert ps.label("Injured Reserve") == "IR"
    assert ps.label("Physically Unable to Perform") == "PUP"
    assert ps.label("Suspension") == "Sus"
    assert ps.label("Day-To-Day") == "DTD"
    assert ps.label("Active") == ps.HEALTHY and ps.label("") == ps.HEALTHY


def test_espn_entries_keep_fantasy_positions_and_designations():
    got = ps.espn_entries(payload(("1", "A", "WR", "Out", "Knee", "IR"), ("2", "B", "LB", "Out", "Knee", None),
                                  ("3", "C", "RB", "Questionable", "Ankle", None)))
    assert set(got) == {"1", "3"}
    assert got["1"]["status"] == "IR" and got["3"]["status"] == "Questionable" and got["3"]["body_part"] == "Ankle"


def test_worse_status_counts_at_once():
    held = {"9": {"sleeper_id": "9", "status": "Questionable"}}
    up, ch = ps.step(held, {"9": {"status": "Out", "body_part": "Ankle"}}, T0)
    assert [(c["from_status"], c["to_status"]) for c in ch] == [("Questionable", "Out")] and up[0]["status"] == "Out"


def test_worse_status_waits_when_the_report_is_down():
    held = {"9": {"sleeper_id": "9", "status": "Questionable"}}
    up, ch = ps.step(held, {"9": {"status": "Out", "body_part": "Ankle"}}, T0, instant=False)
    assert not ch and up[0]["pending"] == "Out"


def test_better_status_needs_two_reads():
    held = {"9": {"sleeper_id": "9", "status": "Out"}}
    feed = {"9": {"status": "Questionable", "body_part": "Ankle"}}
    up, ch = ps.step(held, feed, T0)
    assert not ch and up[0]["pending"] == "Questionable"
    held["9"].update(pending="Questionable", pending_since=T0.isoformat())
    up, ch = ps.step(held, feed, T0 + timedelta(minutes=1))
    assert not ch
    up, ch = ps.step(held, feed, T0 + timedelta(minutes=2))
    assert [(c["from_status"], c["to_status"]) for c in ch] == [("Out", "Questionable")]


def test_blip_back_cancels_pending():
    held = {"9": {"sleeper_id": "9", "status": "Out", "pending": "Healthy", "pending_since": T0.isoformat()}}
    up, ch = ps.step(held, {"9": {"status": "Out", "body_part": ""}}, T0 + timedelta(minutes=5))
    assert not ch and up[0]["pending"] is None


def test_dropping_off_the_report_is_healthy():
    held = {"9": {"sleeper_id": "9", "status": "IR", "pending": "Healthy", "pending_since": T0.isoformat()}}
    _, ch = ps.step(held, {}, T0 + timedelta(minutes=5))
    assert ch[0]["to_status"] == "Healthy"


def test_new_player_on_the_report_counts_at_once():
    up, ch = ps.step({}, {"5": {"status": "Out", "body_part": "Hamstring", "espn_id": "e5"}}, T0)
    assert ch[0]["to_status"] == "Out" and up[0]["status"] == "Out" and up[0]["espn_id"] == "e5"
    up, ch = ps.step({}, {"5": {"status": "Out", "body_part": "Hamstring", "espn_id": "e5"}}, T0, instant=False)
    assert not ch and up[0]["status"] == ps.HEALTHY and up[0]["pending"] == "Out"


def test_short_feed_is_skipped():
    held = {str(i): {"status": "Out"} for i in range(100)}
    assert not ps.feed_ok({"1": {}}, held)
    assert ps.feed_ok({}, {})


def test_alert_wording():
    names = {"1": "Darren Waller", "2": "Mike Evans"}
    a = alerts.status_change(changes=[{"id": 4, "sleeper_id": "1", "from_status": "Healthy", "to_status": "Questionable", "body_part": "Hip"}], names=names)
    assert (a.kind, a.title, a.url) == ("status", "\u26a0\ufe0f Darren Waller is Questionable", "/player/1?section=health")
    assert a.body == "He's listed with a hip injury."
    a = alerts.status_change(changes=[{"id": 5, "sleeper_id": "2", "from_status": "IR", "to_status": "Healthy", "body_part": "Knee"}], names=names)
    assert (a.title, a.body) == ("\u2705 Mike Evans is Healthy", "He's off the injury report.")
    a = alerts.status_change(changes=[{"id": 6, "sleeper_id": "1", "from_status": "Out", "to_status": "IR"},
                                      {"id": 7, "sleeper_id": "2", "from_status": "Healthy", "to_status": "Out"}], names=names)
    assert a.title == "\U0001f4cb 2 of your players changed status" and a.body == "\U0001f3e5 Darren Waller: IR\n\U0001f6d1 Mike Evans: Out" and a.key == "status:6-7"
    assert "status" in alerts.KINDS and "status" in alerts.NOT_LIMITED


def test_game_tag_reset_after_the_game_is_quiet():
    pa = ps
    c = {"to_status": "Healthy", "from_status": "Questionable"}
    assert pa.game_tag_expired(c, "LAR", {"LAR"})
    assert not pa.game_tag_expired(c, "LAR", {"SF"})
    assert not pa.game_tag_expired({"to_status": "Healthy", "from_status": "IR"}, "LAR", {"LAR"})
    assert not pa.game_tag_expired({"to_status": "Out", "from_status": "Questionable"}, "LAR", {"LAR"})


class _Q:
    def __init__(self, db, table):
        self.db, self.t, self.f, self.op, self.payload = db, table, [], "select", None

    def select(self, *_):
        return self

    def order(self, *_, **__):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def limit(self, n):
        return self

    @property
    def not_(self):
        self.neg = True
        return self

    def is_(self, col, _):
        self.f.append(lambda r: r.get(col) is not None)
        return self

    def eq(self, col, v):
        self.f.append(lambda r: str(r.get(col)) == str(v))
        return self

    def in_(self, col, vs):
        self.f.append(lambda r: str(r.get(col)) in {str(x) for x in vs})
        return self

    def upsert(self, rows, on_conflict):
        self.op, self.payload = "upsert", rows
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, row):
        self.op, self.payload = "update", row
        return self

    def execute(self):
        rows = self.db.setdefault(self.t, [])
        if self.op == "upsert":
            for r in self.payload:
                hit = next((x for x in rows if x["sleeper_id"] == r["sleeper_id"]), None)
                hit.update(r) if hit else rows.append(dict(r))
            return type("R", (), {"data": self.payload})
        if self.op == "insert":
            out = [{**r, "id": len(rows) + i + 1} for i, r in enumerate(self.payload)]
            rows += out
            return type("R", (), {"data": out})
        hits = [r for r in rows if all(f(r) for f in self.f)]
        if self.op == "update":
            for r in hits:
                r.update(self.payload)
            return type("R", (), {"data": hits})
        a, b = getattr(self, "rng", (0, 10**6))
        return type("R", (), {"data": hits[a:b + 1]})


class _DB:
    def __init__(self):
        self.db = {"nfl_player_status": [], "nfl_status_changes": [], "nfl_injuries": [],
                   "nfl_players": [{"sleeper_id": "11", "search_name": "a b", "position": "WR", "team": "LAR", "injury_status": "Out"},
                                   {"sleeper_id": "12", "search_name": "c d", "position": "RB", "team": "LAR", "injury_status": None}]}

    def table(self, t):
        return _Q(self.db, t)


def test_refresh_end_to_end(monkeypatch):
    monkeypatch.setattr(ps, "crosswalk", lambda: None)
    db = _DB()
    feed = {"e1": {"status": "Healthy", "body_part": "", "date": "", "name": "A B", "position": "WR", "team": "LAR"},
            "e2": {"status": "Questionable", "body_part": "Ankle", "date": "x", "name": "C D", "position": "RB", "team": "LAR"}}
    monkeypatch.setattr(ps, "fetch_feed", lambda get, cache=None: ({k: dict(v) for k, v in feed.items()}, True))
    assert ps.refresh(db, None, T0) == []  # first read: taken as is
    players = {r["sleeper_id"]: r for r in db.db["nfl_players"]}
    assert players["11"]["injury_status"] is None and players["12"]["injury_status"] == "Questionable"
    feed["e2"]["status"] = "Out"  # worse: counts on this read
    got = ps.refresh(db, None, T0 + timedelta(minutes=1))
    assert [(c["sleeper_id"], c["from_status"], c["to_status"]) for c in got] == [("12", "Questionable", "Out")]
    assert players["12"]["injury_status"] == "Out" and players["12"]["injury_body_part"] == "Ankle"
    del feed["e2"]  # off the report: healthy only after a second read
    assert ps.refresh(db, None, T0 + timedelta(minutes=2)) == [] and players["12"]["injury_status"] == "Out"
    got = ps.refresh(db, None, T0 + timedelta(minutes=4))
    assert [(c["from_status"], c["to_status"]) for c in got] == [("Out", "Healthy")] and players["12"]["injury_status"] is None


def test_alert_without_injury_is_still_a_full_sentence():
    a = alerts.status_change(changes=[{"id": 1, "sleeper_id": "1", "from_status": "Healthy", "to_status": "Sus", "body_part": ""}], names={"1": "Darren Waller"})
    assert (a.title, a.body) == ("\u26d4 Darren Waller is Suspended", "He's suspended by the NFL.")
    a = alerts.status_change(changes=[{"id": 2, "sleeper_id": "1", "from_status": "Out", "to_status": "IR", "body_part": ""}], names={"1": "Darren Waller"})
    assert (a.title, a.body) == ("\U0001f3e5 Darren Waller is on IR", "No injury listed yet.")


def test_merge_keeps_the_injury_from_either_source():
    got = ps.merge({"e": {"status": "Questionable", "body_part": "Hip", "date": "1"}}, {"e": {"status": "Questionable", "body_part": "", "date": "2"}})
    assert got["e"]["body_part"] == "Hip"
    got = ps.merge({"e": {"status": "Out", "body_part": "", "date": "3"}}, {"e": {"status": "Q", "body_part": "Knee", "date": "2"}})
    assert got["e"]["body_part"] == "Knee"


def test_held_injury_used_when_feed_drops_it():
    held = {"9": {"sleeper_id": "9", "status": "Questionable", "body_part": "Hip"}}
    _, ch = ps.step(held, {"9": {"status": "Out", "body_part": ""}}, T0)
    assert ch[0]["body_part"] == "Hip"


def test_fill_body_parts_from_tables():
    db = _DB()
    db.db["nfl_injuries"] = [{"sleeper_id": "7", "injury_type": "", "location": "Hip", "detail": ""}]
    db.db["nfl_practice_reports"] = [{"sleeper_id": "8", "season": 2026, "week": 11, "report_injury": "Knee", "practice_injury": ""}]
    ch = [{"sleeper_id": "7", "to_status": "Questionable", "body_part": ""}, {"sleeper_id": "8", "to_status": "Out", "body_part": ""}]
    ps.fill_body_parts(db, ch)
    assert [c["body_part"] for c in ch] == ["Hip", "Knee"]
