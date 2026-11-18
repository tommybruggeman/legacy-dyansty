from league_ai.power import PlayerStrength
from league_ai.trade_finder import League, find_sales, find_trades, recent_modes

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]
TEAMS = ("me", "buyer", "other")


def _p(team, sid, pos, now, age=27, cap=10.0, dyn=None):
    p = PlayerStrength(sleeper_id=sid, name=f"{pos} {sid}", position=pos, team_id=team, owner=team, now=now, dynasty=dyn if dyn is not None else now,
                       available=True, season_ppg=now, pos_rank=20, years_left=2, age=age)
    p.cap_hit = cap
    return p


def _league(windows=None):
    players = [
        _p("me", "me-qb", "QB", 18), _p("me", "me-rb0", "RB", 20, age=29, cap=30), _p("me", "me-rb1", "RB", 12), _p("me", "me-rb2", "RB", 14), _p("me", "me-wr0", "WR", 13),
        _p("me", "me-wr1", "WR", 12), _p("me", "me-te", "TE", 9),
        _p("buyer", "b-qb", "QB", 20), _p("buyer", "b-rb0", "RB", 8), _p("buyer", "b-rb1", "RB", 7), _p("buyer", "b-wr0", "WR", 17),
        _p("buyer", "b-wr1", "WR", 16), _p("buyer", "b-te", "TE", 10), _p("buyer", "b-young", "WR", 6, age=22, cap=2, dyn=14),
        _p("other", "o-qb", "QB", 15), _p("other", "o-rb0", "RB", 14), _p("other", "o-rb1", "RB", 13), _p("other", "o-wr0", "WR", 14),
        _p("other", "o-wr1", "WR", 13), _p("other", "o-te", "TE", 9),
    ]
    for p in players:
        p.market_value = p.dynasty * 400
    teams = [{"league_team_id": t, "owner_name": t.title(), "team_name": t} for t in TEAMS]
    picks = [{"draft_year": 2027, "round_number": r, "current_owner_league_team_id": t, "original_league_team_id": t} for r in (1, 2) for t in ("buyer", "me")]
    lg = League(players, teams, SLOTS, windows=windows, pick_values=[("2027 1st", 6000.0), ("2027 2nd", 2500.0)], picks=picks)
    return lg


def test_selling_finds_a_buyer_playing_for_now():
    lg = _league({"me": "rebuilding", "buyer": "contending", "other": "middle"})
    out = find_sales(league=lg, asker_team_id="me", cap_space={t: 40.0 for t in TEAMS}, player="RB me-rb0")
    assert out["ideas"], out
    idea = out["ideas"][0]
    assert idea["partner"] == "Buyer" and idea["you_send"].startswith("RB me-rb0")
    assert idea["ask_for"] and "start for them" in idea["why_they_buy"]
    assert "$30 more cap room" in idea["what_it_does"]["for_you"]
    assert "win" not in str(idea["what_it_does"]).lower()


def test_no_sale_to_a_rebuilder():
    lg = _league({"me": "rebuilding", "buyer": "rebuilding", "other": "rebuilding"})
    out = find_sales(league=lg, asker_team_id="me", cap_space={t: 40.0 for t in TEAMS}, player="RB me-rb0")
    assert out["ideas"] == []


def test_all_in_only_takes_big_upgrades():
    lg = _league({"me": "contending", "buyer": "middle", "other": "middle"})
    out = find_trades(league=lg, asker_team_id="me", cap_space={t: 40.0 for t in TEAMS}, goal="all_in", want_position="WR")
    assert "all in" in out["mode"]
    assert out["ideas"]
    for idea in out["ideas"]:
        assert idea["your_lineup_gain"] >= 2.0


def test_recent_modes_read_selling_and_buying():
    rows = [{"event_id": "sleeper:1:a", "season": 2026, "details": {"players": [{"to": "B", "from": "A"}, {"to": "B", "from": "A"}],
                                                                       "picks": [{"to": "A", "from": "B"}, {"to": "A", "from": "B"}]}},
            {"event_id": "sleeper:2:a", "season": 2025, "details": {"picks": [{"to": "C", "from": "D"}] * 3}}]
    assert recent_modes(rows, 2026) == {"A": "selling", "B": "all_in"}
