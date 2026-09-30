import unittest

from league_ai.memory import Memory


class FakeTable:
    def __init__(self, store, name):
        self.store, self.name = store, name
        self.rows = list(store.setdefault(name, []))
        self._pending = None
        self._filters = []

    def select(self, *_a, **_k): return self
    def eq(self, col, val): self.rows = [r for r in self.rows if r.get(col) == val]; return self
    def ilike(self, col, pat):
        needle = pat.strip("%").lower(); self.rows = [r for r in self.rows if needle in str(r.get(col, "")).lower()]; return self
    def order(self, col, desc=False): self.rows.sort(key=lambda r: r.get(col) or "", reverse=desc); return self
    def limit(self, n): self.rows = self.rows[:n]; return self
    def insert(self, row):
        row = dict(row); row.setdefault("id", f"id{len(self.store[self.name]) + 1}"); row.setdefault("created_at", f"2026-09-{29 - len(self.store[self.name]):02d}")
        self.store[self.name].append(row); self._pending = [row]; return self
    def update(self, patch):
        self._patch = patch; return self
    def execute(self):
        class R: pass
        r = R()
        if self._pending is not None:
            r.data = self._pending
        elif hasattr(self, "_patch"):
            for row in self.store[self.name]:
                if row in self.rows:
                    row.update(self._patch)
            r.data = []
        else:
            r.data = [dict(x) for x in self.rows]
        return r


class FakeClient:
    def __init__(self): self.store = {}
    def table(self, name): return FakeTable(self.store, name)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.tommy = Memory(self.client, league_id="L", user_id="u1", league_team_id="t1", owner_name="Tommy")
        self.chase = Memory(self.client, league_id="L", user_id="u2", league_team_id="t2", owner_name="Chase")

    def test_owner_notes_are_private_and_league_notes_shared(self):
        self.assertEqual(self.tommy.remember("owner", "Wants to contend in 2026; will not trade Bijan.")["saved"], True)
        self.assertTrue(self.tommy.remember("league", "Chase overpays for young WRs.")["saved"])
        self.assertEqual(len(self.tommy.private_notes()), 1)
        self.assertEqual(self.chase.private_notes(), [])
        self.assertEqual(len(self.chase.league_notes()), 1)
        self.assertIn("Chase overpays", self.chase.league_notes()[0])

    def test_validation(self):
        self.assertIn("error", self.tommy.remember("team", "x"))
        self.assertIn("error", self.tommy.remember("owner", ""))

    def test_forget_deactivates(self):
        self.tommy.remember("owner", "Will not trade Bijan.")
        out = self.tommy.forget("trade Bijan")
        self.assertEqual(out["forgot"], ["Will not trade Bijan."])
        self.assertEqual(self.tommy.private_notes(), [])

    def test_conversations_round_trip(self):
        cid = self.tommy.save_conversation(None, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
        self.assertTrue(cid)
        cid2 = self.tommy.save_conversation(cid, [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}, {"role": "user", "content": "more"}])
        self.assertEqual(cid, cid2)
        latest = self.tommy.latest_conversation()
        self.assertEqual(len(latest["messages"]), 3)
        self.assertEqual(latest["title"], "hi")
        self.assertIsNone(self.chase.latest_conversation())

    def test_tools(self):
        names = [t.name for t in self.tommy.tools()]
        self.assertEqual(names, ["remember", "forget"])


if __name__ == "__main__":
    unittest.main()
