import unittest
from unittest import mock

from scripts import notify_trades as nt


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters = db, name, {}

    def select(self, *_a):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def in_(self, k, v):
        self.filters[k] = set(v)
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        rows = rows if isinstance(rows, list) else [rows]
        self.db.setdefault(self.name, []).extend(rows)
        return self

    def delete(self):
        self.deleting = True
        return self

    def execute(self):
        rows = [r for r in self.db.get(self.name, []) if all((r.get(k) in v) if isinstance(v, set) else r.get(k) == v for k, v in self.filters.items())]
        return mock.Mock(data=rows)


class _Client:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return _Q(self.db, name)


TX = {"transaction_id": "t9", "type": "trade", "status": "complete", "status_updated": 1, "roster_ids": [1, 2],
      "adds": {"100": 1, "200": 2}, "drops": {"100": 2, "200": 1}, "draft_picks": [{"season": "2027", "round": 1, "owner_id": 1}], "waiver_budget": []}


class NotifyTradesTests(unittest.TestCase):
    def test_message_names_the_league(self):
        self.assertEqual(nt.trade_message("Legacy Dynasty"), "A trade was completed in Legacy Dynasty")

    def _run(self, db):
        def get(url):
            if "/transactions/" in url:
                return [TX] if url.endswith("/8") else []
            return []
        with mock.patch.object(nt, "_get", side_effect=get):
            res = nt.run_league(_Client(db), {"id": "L", "sleeper_league_id": "S"}, 8, "Legacy Dynasty")
        return res, [r for r in db.get("push_outbox", [])]

    def test_first_run_is_quiet_then_new_trades_are_queued_once(self):
        db = {"league_memberships": [{"user_id": "u1", "league_id": "L"}, {"user_id": "u2", "league_id": "L"}]}
        res, queued = self._run(db)
        self.assertEqual(res, {"new": 1, "sent": 0})
        self.assertEqual(queued, [])
        db["push_sent"] = [r for r in db["push_sent"] if r["key"] == "seeded"]  # pretend the trade is new after seeding
        res, queued = self._run(db)
        self.assertEqual(res, {"new": 1, "sent": 2})
        self.assertEqual({(r["user_id"], r["body"], r["url"], r["kind"]) for r in queued},
                         {("u1", "A trade was completed in Legacy Dynasty", "/activity?focus=t9", "trades"),
                          ("u2", "A trade was completed in Legacy Dynasty", "/activity?focus=t9", "trades")})
        res, queued = self._run(db)
        self.assertEqual(res, {"new": 0, "sent": 0})


if __name__ == "__main__":
    unittest.main()
