from api import trade_feed

DETAILS = {"participants": ["Grady", "Tommy"],
           "players": [{"player": "A", "position": "RB", "from": "Tommy", "to": "Grady"}, {"player": "B", "position": "WR", "from": "Grady", "to": "Tommy"}],
           "picks": [{"pick": "2027 R1", "from": "Grady", "to": "Tommy", "original_owner": "Grady"}],
           "faab": [{"amount": "$3 FAAB", "from": "Grady", "to": "Tommy"}]}
ROWS = [
    {"event_id": "sleeper:9:p0", "season": 2026, "week": 5, "occurred_at": "2026-10-05T12:00:00Z", "owner_name": "Grady", "player_id": "1",
     "player_name": "A", "position": "RB", "contract_salary": 30, "contract_years": 2, "details": DETAILS},
    {"event_id": "sleeper:9:p1", "season": 2026, "week": 5, "occurred_at": "2026-10-05T12:00:00Z", "owner_name": "Tommy", "player_id": "2",
     "player_name": "B", "position": "WR", "contract_salary": 12, "contract_years": 1, "details": DETAILS},
    {"event_id": "sleeper:9:k0", "season": 2026, "week": 5, "occurred_at": "2026-10-05T12:00:00Z", "owner_name": "Tommy", "player_id": None,
     "player_name": "2027 R1 pick (orig. Grady)", "position": "PICK", "details": DETAILS},
    {"event_id": "sleeper:4:p0", "season": 2025, "week": 2, "occurred_at": "2025-09-10T12:00:00Z", "owner_name": "Tommy", "player_id": "3",
     "player_name": "C", "position": "QB", "details": {"participants": ["Nando", "Tommy"], "players": [{"player": "C", "from": "Nando", "to": "Tommy"}]}},
]


def _side(trade, owner):
    return next(s for s in trade["sides"] if s["owner"] == owner)


def test_group_builds_one_trade_per_transaction_newest_first():
    feed = trade_feed.group(ROWS)
    assert [t["id"] for t in feed] == ["9", "4"]
    tommy, grady = _side(feed[0], "Tommy"), _side(feed[0], "Grady")
    assert [p["name"] for p in tommy["players"]] == ["B"]
    assert tommy["picks"] == [{"label": "2027 R1", "original_owner": "Grady"}]
    assert tommy["faab"] == 3 and grady["faab"] == -3
    assert tommy["cap_change"] == -18 and grady["cap_change"] == 18


def test_points_since_count_from_the_trade_week():
    feed = trade_feed.group(ROWS)[:1]
    pts = {5: {"1": 10.0, "2": 4.0}, 6: {"1": 20.0, "2": 6.0}, 4: {"1": 99.0}}
    trade_feed.add_points_since(feed, {2026: "L"}, (2026, 6), lambda lid, week, final: pts.get(week, {}))
    trade_feed.add_verdict(feed)
    assert _side(feed[0], "Grady")["points_since"] == 30.0 and _side(feed[0], "Tommy")["points_since"] == 10.0
    assert feed[0]["winning"] == {"owner": "Grady", "by": 20.0}


def test_no_verdict_before_a_week_is_played():
    feed = trade_feed.group(ROWS)[:1]
    trade_feed.add_points_since(feed, {2026: "L"}, (2026, 4), lambda lid, week, final: {})
    trade_feed.add_verdict(feed)
    assert feed[0]["winning"] is None


def test_market_share_skips_drafted_picks():
    feed = trade_feed.group(ROWS)
    trade_feed.add_market_shares(feed, {"1": 3000.0, "2": 1000.0, "3": 500.0}, {(2027, 1): 2000.0}, next_draft=2027)
    assert _side(feed[0], "Tommy")["market_now"] == 3000 and _side(feed[0], "Tommy")["market_share"] == 0.5
    trade_feed.add_market_shares(feed, {"1": 3000.0, "2": 1000.0}, {(2027, 1): 2000.0}, next_draft=2028)
    assert _side(feed[0], "Tommy")["market_now"] == 1000


def test_season_leagues_walks_back():
    leagues = {"new": {"season": "2026", "previous_league_id": "old"}, "old": {"season": "2025", "previous_league_id": None}}
    assert trade_feed.season_leagues("new", lambda path, ttl=0: leagues.get(path.split("/")[1])) == {2026: "new", 2025: "old"}
