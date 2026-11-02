import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import jwt

from api import apns, live_activity

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import live_activity_watcher as watcher  # noqa: E402


def _result(my_points=24.6, final=False, status="playing"):
    return {
        "week": 10, "matchup_id": 3, "is_final": final, "win_chance": [68, 32],
        "teams": [
            {"roster_id": 1, "name": "Tommy", "score": round(62.8 + my_points, 1), "projected": 124.2, "players": [
                {"player_id": "10", "name": "Ja'Marr Chase", "points": my_points, "status": status, "team": "CIN",
                 "line": [{"text": "7/9 REC"}, {"text": "98 YD"}, {"text": "1 TD"}],
                 "game": {"state": "in", "quarter": 2, "score": 17, "opp_score": 10}},
                {"player_id": "11", "name": "Josh Allen", "points": 0, "status": "waiting", "team": "BUF",
                 "kickoff": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()},
            ]},
            {"roster_id": 2, "name": "Dylan", "score": 71.9, "projected": 116.0, "players": [
                {"player_id": "20", "name": "Bijan Robinson", "points": 21.3, "status": "final", "team": "ATL"},
            ]},
        ],
    }


def test_props_put_me_first_with_my_odds():
    p = live_activity.props(_result(), my_roster_id=2)
    assert p["me"]["name"] == "DYLAN" and p["them"]["name"] == "TOMMY"
    assert p["odds"] == 32
    assert p["me"] == {"name": "DYLAN", "score": 71.9, "proj": 116, "playing": 0, "left": 0}
    assert p["last"]["who"] == "B. Robinson" and p["last"]["what"] == "top scorer"


def test_latest_change_shows_the_biggest_move_with_stats_and_game():
    before = live_activity.player_points(_result(my_points=15.2))
    p = live_activity.props(_result(my_points=24.6), my_roster_id=1, before=before)
    assert p["last"] == {"pts": "+9.4", "good": True, "mine": True, "who": "J. Chase", "what": "7/9 REC 98 YD 1 TD",
                         "game": "CIN 17–10 Q2"}
    assert p["me"]["playing"] == 1 and p["me"]["left"] == 1


def test_payload_is_expo_widgets_content_state_and_small():
    p = live_activity.props(_result(), my_roster_id=1)
    body = live_activity.apns_payload("update", p, now=1000, stale_after=900)
    aps = body["aps"]
    assert aps["event"] == "update" and aps["timestamp"] == 1000 and aps["stale-date"] == 1900
    assert aps["content-state"]["name"] == "LiveMatchup"
    assert json.loads(aps["content-state"]["props"]) == p
    assert len(json.dumps(body)) < 4096
    end = live_activity.apns_payload("end", p, now=1000, dismiss_after=1800)
    assert end["aps"]["dismissal-date"] == 2800


def test_is_live_and_changed():
    assert live_activity.is_live(_result())
    quiet = _result(status="final")
    assert not live_activity.is_live(quiet)  # next kickoff is 3 hours out
    assert not live_activity.is_live(_result(final=True))
    a = live_activity.props(_result(), 1)
    assert live_activity.changed(None, a) and not live_activity.changed(a, dict(a))


def test_apns_bearer_is_es256_and_cached():
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    c = apns.Client(key_p8=pem, key_id="ABC123DEFG")
    tok = c.bearer(now=1000)
    assert jwt.get_unverified_header(tok)["kid"] == "ABC123DEFG"
    assert jwt.decode(tok, key.public_key(), algorithms=["ES256"])["iss"] == apns.TEAM_ID
    assert c.bearer(now=1000 + 60) == tok
    assert c.bearer(now=1000 + 51 * 60) != tok
    assert apns.Result(410).gone and apns.Result(400, "BadDeviceToken").gone and not apns.Result(429, "TooManyRequests").gone


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.patch = db, name, None

    def select(self, *a):
        return self

    def is_(self, *a):
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def eq(self, col, val):
        if self.patch is not None:
            self.db.updates.append((val, self.patch))
        return self

    def execute(self):
        from types import SimpleNamespace
        return SimpleNamespace(data=[r for r in self.db.rows if not any(u[0] == r["activity_id"] for u in self.db.updates)])


class _DB:
    def __init__(self, rows):
        self.rows, self.updates = rows, []

    def table(self, name):
        return _Q(self, name)


class _Push:
    def __init__(self, status=200, reason=""):
        self.sent, self.status, self.reason = [], status, reason

    def send_live_activity(self, token, payload, priority=10):
        self.sent.append((token, payload, priority))
        return apns.Result(self.status, self.reason)


def _row(aid="a1"):
    return {"activity_id": aid, "league_id": "L", "roster_id": 1, "week": 10, "matchup_id": 3, "push_token": "ab",
            "started_at": datetime.now(timezone.utc).isoformat()}


def test_watcher_pushes_changes_only_then_ends(monkeypatch):
    results = [_result(my_points=15.2), _result(my_points=15.2), _result(my_points=24.6)]
    monkeypatch.setattr(watcher, "league_ctx", lambda client, lid: {})
    monkeypatch.setattr(watcher, "team_rows", lambda client, lid: [])
    monkeypatch.setattr(watcher.box_score, "game", lambda *a, **k: results.pop(0))
    db, push = _DB([_row()]), _Push()
    w = watcher.Watcher(db, push)
    w.tick(); w.tick(); w.tick()
    assert len(push.sent) == 2  # the unchanged second pass sends nothing
    last = json.loads(push.sent[-1][1]["aps"]["content-state"]["props"])["last"]
    assert last["pts"] == "+9.4" and push.sent[-1][2] == 10
    done = _result(status="final")
    done["teams"][0]["players"][1]["status"] = "final"
    monkeypatch.setattr(watcher.box_score, "game", lambda *a, **k: done)
    w.tick()
    assert push.sent[-1][1]["aps"]["event"] == "end"
    assert db.updates and db.updates[-1][0] == "a1" and "ended_at" in db.updates[-1][1]


def test_watcher_retires_gone_tokens(monkeypatch):
    monkeypatch.setattr(watcher, "league_ctx", lambda client, lid: {})
    monkeypatch.setattr(watcher, "team_rows", lambda client, lid: [])
    monkeypatch.setattr(watcher.box_score, "game", lambda *a, **k: _result())
    db, push = _DB([_row()]), _Push(410, "Unregistered")
    w = watcher.Watcher(db, push)
    w.tick()
    assert w.counts["gone"] == 1 and db.updates[0][0] == "a1"
