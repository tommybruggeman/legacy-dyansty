from league_ai import prospect_rankings as pr


def _row(aid, name, pos, **kw):
    base = {"cfbd_athlete_id": aid, "name": name, "position": pos, "college": "State", "class_year": 3, "height": 75, "weight": 220}
    base.update(kw)
    return base


ROWS = [
    _row("1", "Jeremiah Smith", "WR", receptions=60, rec_yds=1000, rec_td=12, scrimmage_yds=1000),
    _row("2", "Darian Mensah", "QB", pass_att=300, pass_yds=3000, pass_td=25, pass_int=5, rush_yds=40),
    _row("3", "Jadan Baugh", "RB", rush_att=150, rush_yds=900, rush_td=11, receptions=10, rec_yds=80, scrimmage_yds=980),
]


def test_stat_and_size_lines_come_from_our_data():
    assert pr.stat_line(ROWS[1]) == "3,000 pass yds · 25 TD · 5 INT"
    assert pr.stat_line(ROWS[2]).startswith("150 car · 900 rush yds · 11 rush TD")
    assert pr.size_line(ROWS[0]) == "6'3\" · 220 · Jr"


def test_assemble_matches_ids_and_names_and_drops_unknowns():
    raw = {"headline": "H", "players": [
        {"rank": 1, "athlete_id": "1", "name": "J. Smith", "text": "One. Two. Three.", "tags": ["Rising", "Made up"]},
        {"rank": 2, "athlete_id": "", "name": "Darian Mensah", "text": "QB text."},
        {"rank": 3, "athlete_id": "999", "name": "Not In Table", "text": "x"},
        {"rank": 4, "athlete_id": "3", "name": "Jadan Baugh", "text": "RB text."},
    ], "sources": [{"title": "t", "url": "https://example.com"}, {"title": "bad", "url": "javascript:x"}]}
    prev = {"data": {"players": [{"rank": 1, "athlete_id": "3"}, {"rank": 2, "athlete_id": "1"}]}}
    out = pr.assemble(raw, ROWS, prev)
    assert [p["name"] for p in out["players"]] == ["Jeremiah Smith", "Darian Mensah", "Jadan Baugh"]
    assert [p["move"] for p in out["players"]] == ["up", "new", "down"]
    assert out["players"][0]["tags"] == ["Rising"]
    assert out["players"][0]["photo"].endswith("/1.png")
    assert out["sources"] == [{"title": "t", "url": "https://example.com"}]


def test_candidates_keep_last_weeks_board():
    rows = ROWS + [_row("9", "Deep Sleeper", "TE")]
    cands = pr.candidates(rows, {"9"})
    assert {c["cfbd_athlete_id"] for c in cands} >= {"1", "2", "3", "9"}
