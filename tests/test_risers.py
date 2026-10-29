from league_ai import risers


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.filters = db, name, {}

    def select(self, *_):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def limit(self, *_):
        return self

    def insert(self, row):
        self.db.setdefault(self.name, []).append(row)
        return self

    def execute(self):
        rows = [r for r in self.db.get(self.name, []) if all(r.get(k) == v for k, v in self.filters.items())]
        return type("R", (), {"data": rows})()


class _Client:
    def __init__(self):
        self.db = {}

    def table(self, name):
        return _Q(self.db, name)


def test_log_saves_each_week_once():
    client = _Client()
    rows = [{"player_id": "a", "avg": 11.2, "proj": 17.9}, {"player_id": "b", "avg": 12.4, "proj": 18.6}]
    assert risers.log(client, league_id="L", sleeper_id="S", season=2026, week=8, rows=rows)
    assert not risers.log(client, league_id="L", sleeper_id="S", season=2026, week=8, rows=rows)
    saved = client.db["league_ai_experiments"]
    assert len(saved) == 1 and saved[0]["experiment"] == "gm_risers_v2" and saved[0]["result"]["players"][1]["player_id"] == "b"


def test_settle_counts_hits_and_real_rise():
    picks = [{"player_id": "a", "avg": 10.0, "proj": 16.0}, {"player_id": "b", "avg": 12.0, "proj": 15.0}, {"player_id": "c", "avg": 9.0, "proj": 13.0}]
    out = risers.settle(picks, {"a": 18.0, "b": 8.0})
    assert out == {"graded": 2, "hits": 1, "mean_rise": 2.0, "mean_projected_rise": 4.5}
    assert risers.settle(picks, {})["graded"] == 0
