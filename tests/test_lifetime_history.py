import unittest

from api.player import lifetime_history


class LifetimeHistoryTest(unittest.TestCase):
    def test_merges_league_moves_and_skips_duplicates(self):
        contracts = [{"kind": "signed", "title": "Signed", "owner": "A", "date": "Aug 2026", "at": "2026-08-20T00:00:00+00:00", "live": True}]
        events = [
            {"kind": "waiver", "occurred_at": "2026-08-19T12:00:00+00:00", "owner_name": "A", "faab_bid": 3},  # same move as the signing
            {"kind": "draft_pick", "season": 2023, "occurred_at": "2023-05-01T00:00:00+00:00", "owner_name": "B", "details": {"round": 1, "slot": 4}},
            {"kind": "trade", "occurred_at": "2024-10-01T00:00:00+00:00", "owner_name": "C"},
            {"kind": "lineup", "occurred_at": "2024-10-02T00:00:00+00:00", "owner_name": "C"},
        ]
        rows = lifetime_history(contracts, events)
        self.assertEqual([(r["title"], r["owner"]) for r in rows], [("Signed", "A"), ("Traded for", "C"), ("Drafted 1.04", "B")])
        self.assertEqual(rows[1]["kind"], "traded")


if __name__ == "__main__":
    unittest.main()
