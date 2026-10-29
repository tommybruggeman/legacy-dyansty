import unittest
from unittest import mock

from pipeline.league_history.build import OwnerMap
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

    def upsert(self, rows, on_conflict=None):
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
    def test_message_lists_what_each_side_gets(self):
        owners = OwnerMap([], [{"roster_id": 1}, {"roster_id": 2}], [{"sleeper_roster_id": 1, "owner_name": "Tommy Bruggeman"}, {"sleeper_roster_id": 2, "owner_name": "Grady M"}])
        title, body = nt.trade_message(TX, owners, {"100": "Justin Jefferson", "200": "Josh Allen"})
        self.assertEqual(title, "Trade completed")
        self.assertEqual(body, "Tommy B. gets Justin Jefferson, 2027 R1 · Grady M. gets Josh Allen")

    def _run(self, db):
        def get(url):
            if "/transactions/" in url:
                return [TX] if url.endswith("/8") else []
            return []
        with mock.patch.object(nt, "_get", side_effect=get), mock.patch.object(nt, "send", return_value=[]) as send:
            res = nt.run_league(_Client(db), {"id": "L", "sleeper_league_id": "S"}, 8)
        return res, send

    def test_first_run_is_quiet_then_new_trades_are_sent_once(self):
        db = {"push_tokens": [{"token": "ExponentPushToken[a]", "league_id": "L"}], "league_teams": [], "nfl_players": []}
        res, send = self._run(db)
        self.assertEqual(res, {"new": 1, "sent": 0})
        send.assert_not_called()
        db["push_sent"] = [r for r in db["push_sent"] if r["key"] == "seeded"]  # pretend the trade is new after seeding
        res, send = self._run(db)
        self.assertEqual(res, {"new": 1, "sent": 1})
        self.assertEqual(send.call_args[0][0][0]["to"], "ExponentPushToken[a]")
        res, send = self._run(db)
        self.assertEqual(res, {"new": 0, "sent": 0})


if __name__ == "__main__":
    unittest.main()
