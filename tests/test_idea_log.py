import unittest
from datetime import datetime, timezone

from league_ai.idea_log import compact, match, summarize, trades_by_id


class IdeaLogTest(unittest.TestCase):
    def test_compact_keeps_names_and_rungs(self):
        out = {"ideas": [{"partner": "B", "you_get": ["Puka Nacua (WR4, 21.3 ppg, $30 x 2yr)"], "open_with": ["Zay Flowers (WR20)"],
                          "likely_close": ["Zay Flowers (WR20)", "Jake Ferguson (TE9)"]}]}
        self.assertEqual(compact(out), [{"partner": "B", "gets": ["Puka Nacua"], "open_with": ["Zay Flowers"], "likely_close": ["Zay Flowers", "Jake Ferguson"]}])
        target = {"target": "Puka Nacua (WR4)", "partner": "B", "fair_deal_found": True, "open_with": ["Zay Flowers (WR20)"]}
        self.assertEqual(compact(target)[0]["gets"], ["Puka Nacua"])
        self.assertEqual(compact({"target": "X", "partner": "B", "fair_deal_found": False}), [])
        cap = {"ideas": [{"partner": "B", "you_get": ["Puka Nacua (WR4)"],
                          "open_with": ["Zay Flowers (WR20)", "$4 of cap space (as FAAB)"],
                          "likely_close": ["Zay Flowers (WR20)", "2027 2nd pick", "and they add $3 of cap space to you (as FAAB)"]}]}
        row = compact(cap)[0]  # picks and cap aren't players, so they don't count against matching real trades
        self.assertEqual((row["open_with"], row["likely_close"]), (["Zay Flowers"], ["Zay Flowers"]))

    def test_match_finds_the_trade_and_closest_rung(self):
        ev = [{"event_id": "sleeper:t1:a", "owner_name": "A", "counterparty_names": ["B"], "player_name": "Puka Nacua", "occurred_at": "2026-10-10T00:00:00Z"},
              {"event_id": "sleeper:t1:b", "owner_name": "B", "counterparty_names": ["A"], "player_name": "Zay Flowers", "occurred_at": "2026-10-10T00:00:00Z"},
              {"event_id": "sleeper:t1:c", "owner_name": "B", "counterparty_names": ["A"], "player_name": "Jake Ferguson", "occurred_at": "2026-10-10T00:00:00Z"}]
        idea = {"partner": "B", "gets": ["Puka Nacua"], "open_with": ["Zay Flowers"], "likely_close": ["Zay Flowers", "Jake Ferguson"]}
        hit = match(idea, datetime(2026, 10, 5, tzinfo=timezone.utc), trades_by_id(ev))
        self.assertEqual(hit["trade"], "t1")
        self.assertIn(hit["closest_rung"], ("open_with", "likely_close"))
        self.assertIsNone(match(idea, datetime(2026, 10, 20, tzinfo=timezone.utc), trades_by_id(ev)))   # trade came before the idea
        self.assertIsNone(match({**idea, "partner": "C"}, datetime(2026, 10, 5, tzinfo=timezone.utc), trades_by_id(ev)))

    def test_summarize(self):
        rows = [{"result": {"ideas": [{}, {}], "status": "happened", "match": {"closest_rung": "likely_close"}}}, {"result": {"ideas": [{}], "status": "open"}}]
        s = summarize(rows)
        self.assertEqual((s["ideas_shown"], s["happened"], s["still_open"], s["closest_rung"]), (3, 1, 1, {"likely_close": 1}))


if __name__ == "__main__":
    unittest.main()
