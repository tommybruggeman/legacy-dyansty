from scoring import corrections

SCORING = {"rec": 1.0, "rec_yd": 0.1, "rush_td": 6.0}


def line(**stats):
    return {"stats": stats, "pos": "RB"}


def test_team_changes_lists_only_moved_starters():
    old = {"a": line(rec=5, rec_yd=50), "b": line(rush_td=1), "c": line(rec=1)}
    new = {"a": line(rec=6, rec_yd=62), "b": line(rush_td=1), "c": line(rec=9)}  # c is benched
    out = corrections.team_changes({1: ["a", "b"], 2: ["0"]}, old, new, SCORING)
    assert len(out) == 1 and out[0]["team"] == 1
    assert (out[0]["old"], out[0]["new"]) == (16.0, 18.2)
    assert out[0]["players"] == [{"player_id": "a", "old": 10.0, "new": 12.2, "stats": {"rec": [5.0, 6.0], "rec_yd": [50.0, 62.0]}}]


def test_flips_head_to_head_and_top5():
    before = {1: 100.0, 2: 101.0, 3: 90, 4: 80, 5: 70, 6: 60, 7: 50}
    after = {**before, 1: 102.0, 7: 75.0}
    out = corrections.flips(before, after, {1: "m1", 2: "m1", 3: "m2", 4: "m2", 5: "m3", 6: "m3", 7: None})
    assert out[1] == ["win"] and out[2] == ["loss"]
    assert out[7] == ["top5"] and out[5] == ["not_top5"]


def test_no_change_no_correction():
    lines = {"a": line(rec=1)}
    assert corrections.team_changes({1: ["a"]}, lines, dict(lines), SCORING) == []


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.payload = db, table, {}, None

    def select(self, *a): return self
    def eq(self, k, v): self.filters[k] = v; return self
    def lte(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def in_(self, *a): return self
    def delete(self): return self
    def insert(self, row): self.db.setdefault("inserted", []).append(row); self.payload = [{**row, "id": "c1"}]; return self
    def upsert(self, row, **k): self.db.setdefault("upserted", []).append(row); return self

    def execute(self):
        data = self.payload if self.payload is not None else self.db.get(self.table, [])
        return type("R", (), {"data": data})()


class _DB:
    def __init__(self, tables): self.tables = tables
    def table(self, name): return _Q(self.tables, name)


def test_record_corrections_saves_and_pushes(monkeypatch):
    from scripts import final_scores
    tables = {"leagues": [{"id": "L", "sleeper_league_id": "S"}], "league_teams": [{"id": "t1", "sleeper_roster_id": 1}, {"id": "t2", "sleeper_roster_id": 2}],
              "push_sent": [], "push_tokens": [{"token": "tok"}], "league_app_transactions": []}
    pushed = []
    monkeypatch.setattr("scripts.notify_trades.send", lambda msgs: pushed.extend(msgs) or [])
    monkeypatch.setattr("league_ai.settings_store.load_settings", lambda c, l: type("S", (), {"scoring_settings": SCORING})())
    def get(url):
        if url.endswith("/league/S"):
            return {"season": 2026}
        return [{"roster_id": 1, "matchup_id": 1, "starters": ["a"]}, {"roster_id": 2, "matchup_id": 1, "starters": ["b"]}]
    monkeypatch.setattr(final_scores, "_get_json", get)
    old = {"a": line(rec=5), "b": line(rec=6)}
    new = {"a": line(rec=7), "b": line(rec=6)}
    out = final_scores.record_corrections(_DB(tables), 2026, 9, old, new, dry=False)
    saved = tables["inserted"][0]
    assert saved["week"] == 9 and saved["changes"][0]["team_id"] == "t1" and saved["changes"][0]["results"] == ["win"]
    assert any(c["team_id"] == "t2" and "loss" in c["results"] for c in saved["changes"])
    assert pushed and pushed[0]["data"]["url"] == "/activity?correction=c1" and "Week 9" in pushed[0]["body"]
    assert "pushed to 1 phones" in out[-1]
