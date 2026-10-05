import json
import unittest
from types import SimpleNamespace

from league_ai.client import ClaudeClient, Trace, human_message
from league_ai.config import LeagueAIConfig
from league_ai.context_pack import Asker
from league_ai.prompt import system_blocks
from league_ai.service import LeagueDataSources, _history_messages, answer
from league_ai.tools import ToolRegistry, ToolSpec
from tests.fixtures.league_ai_state import LEAGUE_ID, LEAGUE_RULES, SEASON, TOMMY, fixture_standings, fixture_state


CONFIG = LeagueAIConfig(enabled=True, api_key_present=True, model="test-model", max_output_tokens=500, max_tool_calls=3, timeout_seconds=5)


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool_use(i, name, inp):
    return SimpleNamespace(type="tool_use", id=i, name=name, input=inp)


def _resp(content, stop="end_turn", usage=None):
    usage = usage or {}
    return SimpleNamespace(content=content, stop_reason=stop, usage=SimpleNamespace(
        input_tokens=usage.get("in", 100), output_tokens=usage.get("out", 20),
        cache_read_input_tokens=usage.get("cr", 0), cache_creation_input_tokens=usage.get("cc", 0)))


class FakeSDK:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeRateLimit(Exception):
    pass


class FakeTimeoutError(Exception):
    pass


def _sources():
    return LeagueDataSources(client=None, league_id=LEAGUE_ID, season=SEASON, league_name="FLEG",
                             league_rules=LEAGUE_RULES, standings_loader=fixture_standings, state_loader=fixture_state)


def _asker():
    return Asker(user_id="u1", league_id=LEAGUE_ID, league_team_id=TOMMY, owner_name="Tommy Bruggeman")


class HistoryTests(unittest.TestCase):
    def test_history_alternates_and_starts_with_user(self):
        msgs = _history_messages([
            {"role": "assistant", "content": "Welcome"},
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "again"},
            {"role": "assistant", "content": "ok"},
        ])
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0]["content"], "hi\n\nagain")


class ClientLoopTests(unittest.TestCase):
    def test_plain_answer_and_usage(self):
        sdk = FakeSDK([_resp([_text("You have $91.50 of cap space.")], usage={"in": 1000, "out": 30, "cr": 900})])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[{"type": "text", "text": "s"}], messages=[{"role": "user", "content": "cap?"}])
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "You have $91.50 of cap space.")
        self.assertEqual(result.trace.cache_read_tokens, 900)
        self.assertEqual(result.trace.rounds, 1)
        self.assertNotIn("tools", sdk.calls[0])

    def test_on_event_announces_rounds_and_tools(self):
        reg = ToolRegistry()
        reg.register(ToolSpec("get_injury_report", "inj", {"type": "object", "properties": {}}, lambda: {"status": "Out"}))
        sdk = FakeSDK([
            _resp([_tool_use("tu1", "get_injury_report", {})], stop="tool_use"),
            _resp([_text("He's out.")]),
        ])
        events = []
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}], tools=reg,
                                                          on_event=lambda kind, payload: events.append((kind, payload)))
        self.assertTrue(result.ok)
        self.assertEqual(events, [("round", 1), ("reset", None), ("tool", "get_injury_report"), ("round", 2)])

    def test_answer_written_alongside_log_prediction_is_kept(self):
        reg = ToolRegistry()
        reg.register(ToolSpec("log_prediction", "log", {"type": "object", "properties": {}}, lambda **kw: {"logged": True}))
        sdk = FakeSDK([
            _resp([_text("Start Young: the Lions allow 299 passing yards a game."), _tool_use("t1", "log_prediction", {})], stop="tool_use"),
            _resp([_text("Logged at 52%.")]),
        ])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}], tools=reg)
        self.assertEqual(result.text, "Start Young: the Lions allow 299 passing yards a game.\n\nLogged at 52%.")

    def test_streams_text_when_sdk_supports_it(self):
        class _Stream:
            def __init__(self, resp):
                self.resp = resp
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def __iter__(self):
                for chunk in ("He's ", "out."):
                    yield SimpleNamespace(type="text", text=chunk)
            def get_final_message(self):
                return self.resp

        class StreamSDK(FakeSDK):
            def stream(self, **kwargs):
                self.calls.append(kwargs)
                return _Stream(self.responses.pop(0))

        sdk = StreamSDK([_resp([_text("He's out.")])])
        events = []
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}],
                                                          on_event=lambda kind, payload: events.append((kind, payload)))
        self.assertEqual(result.text, "He's out.")
        self.assertEqual(events, [("round", 1), ("text", "He's "), ("text", "out.")])

    def test_effort_sent_only_when_configured(self):
        from dataclasses import replace

        sdk = FakeSDK([_resp([_text("a")]), _resp([_text("b")])])
        ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}])
        ClaudeClient(replace(CONFIG, effort="medium"), sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}])
        self.assertNotIn("output_config", sdk.calls[0])
        self.assertEqual(sdk.calls[1]["output_config"], {"effort": "medium"})

    def test_week_context_goes_after_cached_pack(self):
        blocks = system_blocks("PACK", "## This week\n- NFL week 5")
        self.assertEqual(blocks[1].get("cache_control"), {"type": "ephemeral"})
        self.assertEqual(blocks[2]["text"], "## This week\n- NFL week 5")
        self.assertNotIn("cache_control", blocks[2])
        self.assertEqual(len(system_blocks("PACK")), 2)

    def test_tool_loop_executes_and_feeds_results_back(self):
        reg = ToolRegistry()
        reg.register(ToolSpec("get_player_profile", "profile", {"type": "object", "properties": {"name": {"type": "string"}}},
                              lambda name: {"name": name, "age": 27}))
        sdk = FakeSDK([
            _resp([_text("Let me check."), _tool_use("tu1", "get_player_profile", {"name": "Josh Allen"})], stop="tool_use"),
            _resp([_text("Josh Allen is 27.")]),
        ])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "age?"}], tools=reg)
        self.assertTrue(result.ok)
        self.assertEqual(result.trace.tool_calls, ["get_player_profile"])
        second = sdk.calls[1]["messages"]
        self.assertEqual(second[1]["role"], "assistant")
        self.assertEqual(second[1]["content"][1]["type"], "tool_use")
        self.assertEqual(second[2]["content"][0]["type"], "tool_result")
        self.assertEqual(json.loads(second[2]["content"][0]["content"])["age"], 27)
        self.assertEqual(sdk.calls[0]["tools"][0]["name"], "get_player_profile")

    def test_tool_errors_are_reported_not_raised(self):
        reg = ToolRegistry()
        def boom(**_): raise ValueError("db down")
        reg.register(ToolSpec("get_player_stats", "stats", {"type": "object"}, boom))
        sdk = FakeSDK([
            _resp([_tool_use("tu1", "get_player_stats", {"name": "x"})], stop="tool_use"),
            _resp([_text("I couldn't load stats.")]),
        ])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}], tools=reg)
        self.assertTrue(result.ok)
        self.assertIn("db down", sdk.calls[1]["messages"][2]["content"][0]["content"])

    def test_tool_call_cap_is_enforced(self):
        reg = ToolRegistry()
        reg.register(ToolSpec("t", "t", {"type": "object"}, lambda **_: "ok"))
        responses = [_resp([_tool_use(f"tu{i}", "t", {})], stop="tool_use") for i in range(6)] + [_resp([_text("done")])]
        sdk = FakeSDK(responses)
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}], tools=reg)
        self.assertEqual(len(result.trace.tool_calls), 3)
        limited = [c for call in sdk.calls[4:] for m in call["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                   for c in m["content"] if "limit reached" in c.get("content", "")]
        self.assertTrue(limited)

    def test_errors_map_to_safe_codes_without_secrets(self):
        sdk = FakeSDK([FakeRateLimit("429 for key sk-ant-secret-123")])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}])
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "rate_limited")
        self.assertNotIn("sk-", json.dumps(result.trace.as_dict()))
        sdk = FakeSDK([FakeTimeoutError("slow")])
        self.assertEqual(ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}]).error_code, "timeout")

    def test_missing_key_without_sdk(self):
        cfg = LeagueAIConfig(enabled=True, api_key_present=False, model="m", max_output_tokens=100, max_tool_calls=1, timeout_seconds=1)
        result = ClaudeClient(cfg).run(system=[], messages=[{"role": "user", "content": "q"}])
        self.assertIn(result.error_code, {"missing_api_key", "sdk_unavailable"})
        self.assertTrue(human_message(result.error_code))


class ServiceTests(unittest.TestCase):
    def test_answer_builds_cached_pack_and_appends_question(self):
        sdk = FakeSDK([_resp([_text("Answer.")])])
        result = answer(question="How much cap do I have?", history=[{"role": "assistant", "content": "Welcome"}],
                        asker=_asker(), sources=_sources(), client=ClaudeClient(CONFIG, sdk_client=sdk), config=CONFIG)
        self.assertTrue(result.ok)
        call = sdk.calls[0]
        self.assertEqual(call["system"][1]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Tommy Bruggeman ← you", call["system"][1]["text"])
        self.assertEqual(call["messages"][-1], {"role": "user", "content": "How much cap do I have?"})
        self.assertGreater(result.pack_chars, 1000)
        line = result.trace_line()
        self.assertTrue(line.startswith("LEAGUE_AI_TRACE "))
        self.assertNotIn("sk-", line)

    def test_disabled_and_missing_key(self):
        cfg = LeagueAIConfig(enabled=False, api_key_present=True, model="m", max_output_tokens=100, max_tool_calls=1, timeout_seconds=1)
        self.assertEqual(answer(question="q", history=[], asker=_asker(), sources=_sources(), config=cfg).error_code, "disabled")
        cfg = LeagueAIConfig(enabled=True, api_key_present=False, model="m", max_output_tokens=100, max_tool_calls=1, timeout_seconds=1)
        self.assertEqual(answer(question="q", history=[], asker=_asker(), sources=_sources(), config=cfg).error_code, "missing_api_key")

    def test_system_blocks_shape(self):
        blocks = system_blocks("PACK")
        self.assertEqual(len(blocks), 2)
        self.assertIn("read-only", blocks[0]["text"])
        self.assertEqual(blocks[1]["text"], "PACK")


if __name__ == "__main__":
    unittest.main()


class ThinkingBlockTests(unittest.TestCase):
    def test_thinking_blocks_are_passed_back_in_tool_loop(self):
        reg = ToolRegistry()
        reg.register(ToolSpec("t", "t", {"type": "object"}, lambda **_: "ok"))
        thinking = {"type": "thinking", "thinking": "hmm", "signature": "sig"}
        sdk = FakeSDK([
            _resp([thinking, _tool_use("tu1", "t", {})], stop="tool_use"),
            _resp([_text("done")]),
        ])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}], tools=reg)
        self.assertTrue(result.ok)
        assistant_turn = sdk.calls[1]["messages"][1]["content"]
        self.assertEqual(assistant_turn[0], thinking)
        self.assertEqual(result.trace.content_types, ["text"])

    def test_max_tokens_with_no_text_is_a_budget_error(self):
        sdk = FakeSDK([_resp([{"type": "thinking", "thinking": "..."}], stop="max_tokens")])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}])
        self.assertEqual(result.error_code, "output_budget_exhausted")
        self.assertIn("too big to finish", human_message(result.error_code))


if __name__ == "__main__":
    unittest.main()
