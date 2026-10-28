import unittest

from league_ai import scorecard
from league_ai.start_sit_grade import grade


def _lineup(season, week, owner, starter, bench):
    return {"season": season, "week": week, "owner_name": owner,
            "details": {"starters": [{"id": starter[0], "pos": "WR", "pts": starter[1]}], "bench": [{"id": bench[0], "pos": "WR", "pts": bench[1]}]}}


class ScorecardTest(unittest.TestCase):
    def test_build_and_owner_view(self):
        rows = [_lineup(2026, 1, "Ann", ("a", 5), ("b", 12)), _lineup(2026, 2, "Ann", ("a", 15), ("b", 4)), _lineup(2026, 1, "Bo", ("c", 9), ("d", 3))]
        proj = {"a": 8.0, "b": 10.0, "c": 12.0, "d": 6.0}
        card = scorecard.build(grade(rows, lambda s, w: proj))
        self.assertEqual(card["season"], 2026)
        self.assertEqual(card["overall"]["calls"], 3)
        self.assertEqual([w["week"] for w in card["by_week"]], [1, 2])
        self.assertEqual(card["by_position"][0]["pos"], "WR")
        db = FakeDB({"league_teams": [{"id": "t1", "owner_name": "Ann", "league_id": "L"}, {"id": "t2", "owner_name": "Bo", "league_id": "L"}],
                     "league_memberships": [{"user_id": "u1", "league_team_id": "t1", "league_id": "L"}]})
        self.assertEqual(scorecard.save(db, "L", card), 1)   # Bo has no account: no private line
        shared = db.inserted[0]
        self.assertNotIn("owners", shared["result"])           # other owners' lines never go in the shared row
        self.assertEqual(shared["user_id"], scorecard.SYSTEM_USER)
        self.assertEqual(db.inserted[1]["user_id"], "u1")
        view = scorecard.load(db, "L", "u1")
        self.assertEqual(view["you"]["calls"], 2)
        self.assertNotIn("owners", view)


class FakeDB:
    def __init__(self, tables):
        self.tables, self.inserted = tables, []

    def table(self, name):
        return _Q(self, name)


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.rows = db, name, {}, None

    def select(self, *_):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def order(self, *_, **__):
        return self

    def limit(self, *_):
        return self

    def insert(self, rows):
        rows = rows if isinstance(rows, list) else [rows]
        self.db.inserted += rows
        self.rows = rows
        return self

    def execute(self):
        if self.rows is not None:
            return type("R", (), {"data": self.rows})
        src = self.db.inserted if self.name == "league_ai_experiments" else self.db.tables.get(self.name, [])
        data = [r for r in src if all(r.get(k) == v for k, v in self.filters.items())][::-1]
        return type("R", (), {"data": data})

if __name__ == "__main__":
    unittest.main()
