import unittest
from types import SimpleNamespace

from league_ai import odds_trial
from league_ai.tools import ToolRegistry, ToolSpec


class _Q:
    def __init__(self, store, name, data=None):
        self.store, self.name, self.data = store, name, data

    def insert(self, row):
        return _Q(self.store, self.name, row)

    def execute(self):
        if self.name == "rpc":
            return SimpleNamespace(data=self.data)
        self.store.setdefault(self.name, []).append(self.data)
        return SimpleNamespace(data=[self.data])


class _Client:
    def __init__(self, count=0):
        self.store, self._count = {}, count

    def rpc(self, fn, params):
        return _Q(self.store, "rpc", self._count)

    def table(self, name):
        return _Q(self.store, name)


class _Sdk:
    def __init__(self, text):
        self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)], usage=SimpleNamespace(input_tokens=900, output_tokens=120)))


GOOD = '{"changed": true, "without_answer": "Take it.", "effect": "changed_call", "better": "with", "why": "It costs you 5 points of playoff odds.", "risk": "none"}'


class OddsTrialTests(unittest.TestCase):
    def _tools(self):
        reg = ToolRegistry()
        reg.register(ToolSpec("evaluate_trade", "", {}, lambda **kw: {"partner": "Nick", "playoff_odds": {"you": {"before": 71, "after": 66}}}))
        reg.register(ToolSpec("get_player_stats", "", {}, lambda **kw: {"x": 1}))
        return reg

    def test_watch_records_only_odds(self):
        reg = self._tools()
        seen = odds_trial.watch(reg)
        reg.call("get_player_stats", {})
        self.assertEqual(seen, [])
        out = reg.call("evaluate_trade", {"partner": "Nick"})
        self.assertIn("playoff_odds", out)  # the model still gets the full result
        self.assertEqual(seen[0]["odds"]["you"]["after"], 66)

    def test_runs_logs_and_counts_cost(self):
        client = _Client(count=3)
        ran = odds_trial.maybe_run(client, league_id="L", user_id="U", conversation_id=None, question="Irving for Young?", answer="Pass.",
                                   seen=[{"tool": "evaluate_trade", "args": {}, "odds": {"you": {}}}], sdk_factory=lambda: _Sdk(GOOD), background=False)
        self.assertTrue(ran)
        row = client.store["league_ai_experiments"][0]
        self.assertEqual(row["experiment"], odds_trial.EXPERIMENT)
        self.assertTrue(row["result"]["changed"])
        self.assertGreater(row["cost_usd"], 0)
        self.assertEqual(client.store["league_ai_usage"][0]["feature"], "experiment")

    def test_stops_after_the_limit_and_without_odds(self):
        seen = [{"tool": "get_playoff_odds", "args": {}, "odds": {"odds": {}}}]
        self.assertFalse(odds_trial.maybe_run(_Client(count=20), league_id="L", user_id="U", conversation_id=None, question="q", answer="a",
                                              seen=seen, sdk_factory=lambda: _Sdk(GOOD), background=False))
        self.assertFalse(odds_trial.maybe_run(_Client(), league_id="L", user_id="U", conversation_id=None, question="q", answer="a",
                                              seen=[], sdk_factory=lambda: _Sdk(GOOD), background=False))

    def test_parse_and_summary(self):
        self.assertIsNone(odds_trial.parse("no json here"))
        self.assertTrue(odds_trial.parse("Sure:\n" + GOOD)["changed"])
        rows = [{"created_at": "2026-10-29T10:00", "question": "q", "cost_usd": 0.002, "result": odds_trial.parse(GOOD)},
                {"created_at": "2026-10-29T11:00", "question": "q", "cost_usd": 0.002, "result": {"changed": False, "effect": "no_effect", "better": "same"}}]
        s = odds_trial.summarize(rows)
        self.assertEqual((s["checks"], s["changed_call"]), (2, 1))
        self.assertIn("helped", s["verdict"])
        self.assertIn("2 of 20", s["verdict"])


if __name__ == "__main__":
    unittest.main()
