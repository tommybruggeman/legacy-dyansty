from api import activity

HIST = [
    {"event_id": "sleeper:7:a1", "kind": "waiver", "season": 2025, "week": 3, "occurred_at": "2025-09-20T10:00:00Z", "owner_name": "Grady",
     "player_id": "1", "player_name": "A", "position": "RB", "faab_bid": 12, "details": {"type": "waiver"}},
    {"event_id": "sleeper:7:d2", "kind": "drop", "season": 2025, "week": 3, "occurred_at": "2025-09-20T10:00:00Z", "owner_name": "Grady",
     "player_id": "2", "player_name": "B", "position": "WR", "details": {"type": "waiver"}},
    {"event_id": "sleeper:draft:d1:2", "kind": "draft_pick", "season": 2026, "week": 0, "occurred_at": "2026-07-10T00:00:00Z", "owner_name": "Nando",
     "player_id": "9", "player_name": "R2", "position": "WR", "details": {"round": 1, "slot": 2, "overall": 2}},
    {"event_id": "sleeper:draft:d1:1", "kind": "draft_pick", "season": 2026, "week": 0, "occurred_at": "2026-07-10T00:00:00Z", "owner_name": "Tommy",
     "player_id": "8", "player_name": "R1", "position": "RB", "details": {"round": 1, "slot": 1, "overall": 1}},
    {"event_id": "sleeper:old26:a5", "kind": "add", "season": 2026, "week": 2, "occurred_at": "2026-09-15T00:00:00Z", "owner_name": "Tommy",
     "player_id": "5", "player_name": "Stale", "position": "TE", "details": {"type": "free_agent"}},
]


def test_moves_pair_add_and_drop():
    items = activity.moves(HIST)
    claim = next(i for i in items if i["id"] == "7")
    assert claim["type"] == "waiver" and claim["adds"][0]["faab"] == 12 and [d["name"] for d in claim["drops"]] == ["B"]


def test_drafts_in_pick_order():
    (draft,) = activity.drafts(HIST)
    assert [p["name"] for p in draft["picks"]] == ["R1", "R2"] and draft["season"] == 2026


def test_merge_uses_live_for_this_season_but_keeps_draft_picks():
    live = [{"event_id": "sleeper:99:a6", "kind": "add", "season": 2026, "owner_name": "Tommy"}]
    merged = activity.merge(HIST, live, 2026)
    ids = {r["event_id"] for r in merged}
    assert "sleeper:old26:a5" not in ids and "sleeper:99:a6" in ids and "sleeper:draft:d1:1" in ids and "sleeper:7:a1" in ids
    assert activity.merge(HIST, [], 2026) == HIST


def test_live_rows_reads_sleeper_transactions():
    tx = {"transaction_id": "55", "type": "waiver", "status": "complete", "leg": 8, "status_updated": 1793300000000,
          "adds": {"11": 3}, "drops": {"12": 3}, "settings": {"waiver_bid": 7}, "roster_ids": [3]}
    calls = []

    def get(path, ttl=0):
        calls.append(path)
        if path.endswith("/transactions/8"):
            return [tx]
        if path.endswith("/rosters"):
            return [{"roster_id": 3, "owner_id": "u3"}]
        return []

    rows = activity.live_rows("L", "S", 2026, 8, [{"owner_name": "Grady", "sleeper_roster_id": 3}],
                              lambda ids: {"11": {"full_name": "New Guy", "position": "WR"}, "12": {"full_name": "Old Guy", "position": "RB"}},
                              {"11": {"salary": 1, "years": 1}}, get)
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["drop", "waiver"]
    add = next(r for r in rows if r["kind"] == "waiver")
    assert add["owner_name"] == "Grady" and add["faab_bid"] == 7 and add["player_name"] == "New Guy" and add["contract_salary"] == 1
    assert "league/S/transactions/1" in calls
