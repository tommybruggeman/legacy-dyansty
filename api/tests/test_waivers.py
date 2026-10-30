from datetime import datetime, timezone

from api import waivers

ME, THEM = "t1", "t2"
INFO = {"p1": {"full_name": "Puka Nacua", "position": "WR", "team": "LAR"}}


def test_next_run_and_free_agency_window():
    fri = datetime(2026, 12, 18, 17, 0, tzinfo=timezone.utc)  # Friday noon ET
    assert waivers.run_label(waivers.next_run(fri)) == "Sunday 3 AM"
    sun = datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc)  # Sunday 7 AM ET
    assert waivers.free_agency_open(sun) and not waivers.free_agency_open(fri)
    assert waivers.run_label(waivers.next_run(sun)) == "Wednesday 3 AM"


def test_dead_cap_rules():
    seasons = [{"season": 2026, "cap_hit": 6, "obligation_status": "active"}, {"season": 2027, "cap_hit": 6, "obligation_status": "scheduled"},
               {"season": 2025, "cap_hit": 6, "obligation_status": "released"}]
    cost = waivers.dead_cap(seasons, 2026, 50)
    assert cost == {"this_season": 3.0, "total": 6.0, "through": 2027}
    assert waivers.dead_cap_text(cost, 2026) == "$3 dead cap through 2027"
    one = waivers.dead_cap([{"season": 2026, "cap_hit": 1, "obligation_status": "active"}], 2026, 50)
    assert waivers.dead_cap_text(one, 2026) == "No dead cap"
    assert waivers.dead_cap_text(waivers.dead_cap(seasons[:1], 2026, 50), 2026) == "$3 dead cap this season"


def test_bids_view_open_and_results():
    now = datetime(2026, 12, 20, 12, 0, tzinfo=timezone.utc)
    run = "2026-12-20T08:00:00+00:00"
    rows = [
        {"id": "c1", "league_team_id": ME, "player_id": "p1", "bid": 30, "status": "lost", "process_at": run,
         "decided_at": "2026-12-20T08:00:05+00:00", "winning_team_id": THEM, "winning_bid": 32},
        {"id": "c2", "league_team_id": THEM, "player_id": "p1", "bid": 32, "status": "won", "process_at": run,
         "decided_at": "2026-12-20T08:00:05+00:00", "winning_team_id": THEM, "winning_bid": 32},
        {"id": "c3", "league_team_id": ME, "player_id": "p1", "bid": 5, "status": "pending", "process_at": "2026-12-23T08:00:00+00:00",
         "created_at": "2026-12-20T10:00:00+00:00"},
    ]
    out = waivers.bids_view(rows, ME, {ME: "Tommy B", THEM: "Grady M"}, INFO, now)
    assert out["bids"][0]["runs"] == "Wednesday 3 AM" and out["bids"][0]["bid"] == 5
    res = out["results"][0]
    assert res["line"] == "Grady M outbid you for Puka Nacua: $32 to your $30"
    assert [b["bid"] for b in res["bids"]] == [32, 30] and res["bids"][0]["won"] and res["bids"][1]["mine"]
