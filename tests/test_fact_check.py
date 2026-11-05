import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from league_ai import fact_check, runtime
from league_ai.client import Trace
from league_ai.service import AnswerResult


class FakeSdk:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
                               usage=SimpleNamespace(input_tokens=900, output_tokens=60, cache_read_input_tokens=30000, cache_creation_input_tokens=0))


ANSWER = "Start Pickens over Waddle. Pickens projects 14.2 to Waddle's 11.0, and Waddle costs $21 x 2yr."


class FactCheckTests(unittest.TestCase):
    def test_ok_answer_is_kept(self):
        sdk = FakeSdk({"verdict": "ok", "problems": [], "corrected_answer": ""})
        out = fact_check.check(ANSWER, pack="pack", evidence=[{"tool": "compare_players", "input": {}, "result": "{}"}], sdk_client=sdk)
        self.assertTrue(out.ran)
        self.assertFalse(out.fixed)
        self.assertEqual(out.text, ANSWER)
        kw = sdk.calls[0]
        self.assertEqual(kw["model"], fact_check.MODEL)
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertIn("cache_control", kw["messages"][0]["content"][0])  # the pack is cached

    def test_wrong_number_is_corrected(self):
        fixed = ANSWER.replace("$21", "$18")
        sdk = FakeSdk({"verdict": "fix", "problems": [{"said": "$21 x 2yr", "data_says": "$18 x 2yr"}], "corrected_answer": fixed})
        out = fact_check.check(ANSWER, pack="pack", evidence=[], sdk_client=sdk)
        self.assertTrue(out.fixed)
        self.assertEqual(out.text, fixed)
        self.assertEqual(out.usage["cache_read_tokens"], 30000)

    def test_a_rewrite_is_not_a_fix(self):
        sdk = FakeSdk({"verdict": "fix", "problems": [{"said": "x", "data_says": "y"}], "corrected_answer": "Start Pickens."})
        out = fact_check.check(ANSWER, pack="pack", evidence=[], sdk_client=sdk)
        self.assertFalse(out.fixed)
        self.assertEqual(out.text, ANSWER)
        self.assertEqual(out.error, "fix_rejected")

    def test_errors_fail_open(self):
        out = fact_check.check(ANSWER, pack="pack", evidence=[], sdk_client=FakeSdk(error=RuntimeError("boom")))
        self.assertFalse(out.ran)

    def test_newest_lookups_survive_the_cut(self):
        ev = [{"tool": f"t{i}", "input": {}, "result": "x" * 30000} for i in range(4)]
        text = fact_check._lookups(ev)
        self.assertIn("## t3", text)
        self.assertNotIn("## t0", text)


class ModelPickTests(unittest.TestCase):
    def test_off_by_default_and_stable_per_conversation(self):
        with mock.patch.dict(os.environ, {"LEAGUE_AI_HARD_MODEL": "", "LEAGUE_AI_HARD_SHARE": ""}):
            self.assertIsNone(runtime.pick_model("trade", "c1"))
        with mock.patch.dict(os.environ, {"LEAGUE_AI_HARD_MODEL": "claude-opus-5-5", "LEAGUE_AI_HARD_SHARE": "1"}):
            self.assertEqual(runtime.pick_model("trade", "c1"), "claude-opus-5-5")
            self.assertIsNone(runtime.pick_model("lookup", "c1"))  # only hard questions
        with mock.patch.dict(os.environ, {"LEAGUE_AI_HARD_MODEL": "claude-opus-5-5", "LEAGUE_AI_HARD_SHARE": "0.5"}):
            picks = {runtime.pick_model("strategy", f"c{i}") for i in range(200)}
            self.assertEqual(picks, {"claude-opus-5-5", None})
            self.assertEqual(runtime.pick_model("strategy", "same"), runtime.pick_model("strategy", "same"))


class ChatTurnTests(unittest.TestCase):
    def _run(self, checked, history=None, saved=None):
        events, rows, saved_msgs = [], [], {}

        class Mem:
            def get_conversation(self, cid):
                return saved

            def save_conversation(self, cid, msgs):
                saved_msgs["msgs"] = msgs
                return "conv1"

        seen = {}

        def fake_answer(**kw):
            seen.update(kw)
            return AnswerResult(True, ANSWER, Trace(model="claude-sonnet-5-5"), evidence=[{"tool": "compare_players", "input": {"players": ["a"]}, "result": "proj 14.2"}], pack="P")

        owner = runtime.Owner(user_id="u", league_id="l", league_team_id="t", season=2026, owner_name="Tommy", team_name="T")
        with mock.patch.object(runtime, "answer", fake_answer), mock.patch.object(runtime.Owner, "memory", lambda self, c: Mem()), \
                mock.patch.object(runtime, "record_usage", lambda c, row: rows.append(row)), \
                mock.patch.object(runtime.fact_check, "check", lambda *a, **k: checked), \
                mock.patch.object(runtime.fact_check, "enabled", lambda: True):
            turn = runtime.chat_turn(None, owner, question="why?", history=history or [], conversation_id="conv1" if history else None, pack="P",
                                     tools=None, on_event=lambda k, p: events.append((k, p)))
        return turn, events, rows, saved_msgs, seen

    def test_fixed_answer_replaces_the_draft_and_is_saved_with_its_facts(self):
        fixed = ANSWER.replace("$21", "$18")
        turn, events, rows, saved, _ = self._run(fact_check.Check(ran=True, fixed=True, text=fixed, problems=[{"said": "a", "data_says": "b"}]))
        self.assertEqual(turn.result.text, fixed)
        self.assertEqual(events[-2:], [("reset", None), ("text", fixed)])
        self.assertEqual(saved["msgs"][-1]["content"], fixed)
        self.assertIn("compare_players", saved["msgs"][-1]["facts"])
        self.assertEqual([r["feature"] for r in rows], ["fact_check", "chat"])
        self.assertEqual(rows[0]["topic"], "fixed")

    def test_follow_up_gets_the_last_answers_facts(self):
        saved = {"messages": [{"role": "user", "content": "start?"}, {"role": "assistant", "content": "x", "facts": "compare_players: proj 14.2"}]}
        _, events, _, _, seen = self._run(fact_check.Check(ran=True, text=ANSWER), history=[{"role": "user", "content": "start?"}, {"role": "assistant", "content": "x"}], saved=saved)
        self.assertEqual(seen["prior_facts"], "compare_players: proj 14.2")
        self.assertFalse(any(k == "reset" for k, _ in events))


if __name__ == "__main__":
    unittest.main()


class PriorFactsTests(unittest.TestCase):
    def test_facts_ride_in_front_of_the_follow_up(self):
        from league_ai.client import ClientResult
        from league_ai.config import LeagueAIConfig
        from league_ai.context_pack import Asker
        from league_ai.service import LeagueDataSources, answer

        got = {}

        class Runner:
            def run(self, **kw):
                got.update(kw)
                return ClientResult(True, "ok", Trace())

        cfg = LeagueAIConfig(enabled=True, api_key_present=True, model="m", max_output_tokens=1000, max_tool_calls=1, timeout_seconds=5)
        answer(question="why?", history=[{"role": "user", "content": "start?"}, {"role": "assistant", "content": "Pickens."}],
               asker=Asker("u", "l", "t", "Tommy"), sources=LeagueDataSources(client=None, league_id="l", season=2026), client=Runner(), config=cfg,
               pack="P", prior_facts="compare_players: proj 14.2")
        last = got["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("compare_players: proj 14.2", last["content"])
        self.assertTrue(last["content"].endswith("why?"))
