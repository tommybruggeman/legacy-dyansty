import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from league_ai.client import PER_MESSAGE_EFFORT_BETA, ClaudeClient
from league_ai.routing import effort_for, topic
from league_ai.usage import cost_usd, response_usage, row_from_trace
from league_ai.weekly_report import build_report, render_html
from tests.test_league_ai_learning import MemDB
from tests.test_league_ai_service import CONFIG, FakeSDK, _resp, _text


class RoutingTests(unittest.TestCase):
    def test_lookups_are_low_and_decisions_high(self):
        self.assertEqual(effort_for("What's my cap space?"), "low")
        self.assertEqual(effort_for("How much dead cap if I cut Kirk?"), "low")
        self.assertEqual(effort_for("Who do I start at flex, Pacheco or Warren?"), "high")
        self.assertEqual(effort_for("Should I accept Dylan's trade for my 2027 1st?"), "high")
        self.assertEqual(effort_for("Am I a contender?"), "high")
        self.assertEqual(effort_for("Tell me about the rookie class"), "medium")

    def test_topics(self):
        self.assertEqual(topic("Find me a trade that helps now"), "trade")
        self.assertEqual(topic("Waiver targets and prices"), "waivers")
        self.assertEqual(topic("Who should I start this week?"), "lineup")
        self.assertEqual(topic("What is my record?"), "lookup")


class CostTests(unittest.TestCase):
    def test_list_price_math(self):
        self.assertAlmostEqual(cost_usd("claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=100_000), 3.0)
        self.assertAlmostEqual(cost_usd("claude-sonnet-5-5", cache_read_tokens=1_000_000, cache_write_tokens=1_000_000), 2.7)
        self.assertAlmostEqual(cost_usd("claude-haiku-4-5", input_tokens=1_000_000, batch=True), 0.5)
        self.assertAlmostEqual(cost_usd("claude-sonnet-5-5", web_searches=3), 0.03)
        self.assertIsNone(cost_usd("mystery-model", input_tokens=5))

    def test_row_from_trace_and_response_usage(self):
        trace = SimpleNamespace(as_dict=lambda: {"model": "claude-sonnet-5-5", "effort": "low", "input_tokens": 500, "output_tokens": 300, "cache_read_tokens": 20000,
                                                 "cache_creation_tokens": 0, "tool_calls": ["get_player_stats"], "rounds": 2, "latency_ms": 4000, "error_code": None})
        row = row_from_trace(trace, league_id="L1", user_id="u1", feature="chat", topic="lookup")
        self.assertEqual(row["effort"], "low")
        self.assertTrue(row["ok"])
        self.assertAlmostEqual(row["cost_usd"], (500 * 2 + 300 * 10 + 20000 * 0.2) / 1e6)
        resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                                                     server_tool_use=SimpleNamespace(web_search_requests=2)))
        self.assertEqual(response_usage(resp)["web_searches"], 2)


class PerTurnEffortTests(unittest.TestCase):
    def test_effort_message_sits_before_latest_question_with_beta_header(self):
        sdk = FakeSDK([_resp([_text("ok")])])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
                                                                               {"role": "user", "content": "q2"}], turn_effort="low")
        msgs = sdk.calls[0]["messages"]
        self.assertEqual(msgs[2], {"role": "system", "content": [], "output_config": {"effort": "low"}})
        self.assertEqual(msgs[3]["content"], "q2")
        self.assertEqual(sdk.calls[0]["extra_headers"], {"anthropic-beta": PER_MESSAGE_EFFORT_BETA})
        self.assertNotIn("output_config", sdk.calls[0])
        self.assertEqual(result.trace.effort, "low")

    def test_rejected_beta_falls_back_to_default_effort(self):
        class BadRequestError(Exception):
            pass

        sdk = FakeSDK([BadRequestError("unknown beta"), _resp([_text("ok")])])
        result = ClaudeClient(CONFIG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}], turn_effort="high")
        self.assertTrue(result.ok)
        self.assertNotIn("extra_headers", sdk.calls[1])
        self.assertFalse(any(m.get("role") == "system" for m in sdk.calls[1]["messages"]))
        self.assertIsNone(result.trace.effort)

    def test_fixed_effort_overrides_routing(self):
        from dataclasses import replace

        sdk = FakeSDK([_resp([_text("ok")])])
        ClaudeClient(replace(CONFIG, effort="medium"), sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "q"}], turn_effort="low")
        self.assertEqual(sdk.calls[0]["output_config"], {"effort": "medium"})
        self.assertNotIn("extra_headers", sdk.calls[0])


class WeeklyReportTests(unittest.TestCase):
    def _db(self):
        t = "2026-10-04T18:00:00+00:00"
        return MemDB({
            "league_teams": [{"id": "T1", "league_id": "L1", "owner_name": "Tommy Bruggeman"}, {"id": "T2", "league_id": "L1", "owner_name": "Dylan Burruel"}],
            "league_memberships": [{"league_id": "L1", "user_id": "u1", "league_team_id": "T1"}, {"league_id": "L1", "user_id": "u2", "league_team_id": "T2"}],
            "league_ai_usage": [
                {"league_id": "L1", "created_at": t, "user_id": "u1", "feature": "chat", "model": "claude-sonnet-5-5", "effort": "high", "topic": "lineup",
                 "input_tokens": 1000, "output_tokens": 2000, "cache_read_tokens": 20000, "cache_write_tokens": 0, "tool_calls": ["get_injury_report"],
                 "latency_ms": 9000, "ok": True, "batch": False, "cost_usd": 0.026, "conversation_id": "c1"},
                {"league_id": "L1", "created_at": t, "user_id": "u2", "feature": "chat", "model": "claude-sonnet-5-5", "effort": "low", "topic": "trade",
                 "input_tokens": 500, "output_tokens": 300, "cache_read_tokens": 20000, "cache_write_tokens": 0, "tool_calls": [],
                 "latency_ms": 3000, "ok": True, "batch": False, "cost_usd": 0.008, "conversation_id": "c2"},
                {"league_id": "L1", "created_at": t, "user_id": None, "feature": "grading", "model": "claude-sonnet-5-5", "input_tokens": 9000, "output_tokens": 2000,
                 "web_searches": 2, "ok": True, "batch": True, "cost_usd": 0.039},
                {"league_id": "L1", "created_at": "2026-09-01T00:00:00+00:00", "feature": "chat", "model": "claude-sonnet-5-5", "cost_usd": 1.0, "ok": True},
            ],
            "league_ai_conversations": [
                {"id": "c1", "league_id": "L1", "user_id": "u1", "title": "Who do I start", "updated_at": t, "created_at": t,
                 "messages": [{"role": "user", "content": "Who do I start, Pacheco or Warren?"}, {"role": "assistant", "content": "Pacheco."}]},
                {"id": "c2", "league_id": "L1", "user_id": "u2", "title": "Secret trade plan", "updated_at": t, "created_at": t,
                 "messages": [{"role": "user", "content": "Should I trade for Tommy's Chase?"}]},
            ],
            "league_ai_predictions": [
                {"league_id": "L1", "user_id": "u1", "conversation_id": "c1", "kind": "start_sit", "week": 5, "created_at": t, "status": "graded", "graded_at": t,
                 "pick": [{"name": "Pacheco"}], "over": [{"name": "Warren"}], "correct": False, "pick_points": 6.1, "over_points": 18.4, "cause": "usage_shift",
                 "process_error": True, "diagnosis": "Warren took 70% of snaps."},
                {"league_id": "L1", "user_id": "u2", "conversation_id": "c2", "kind": "trade", "verdict": "accept", "week": 5, "created_at": t, "status": "open",
                 "pick": [{"name": "Ja'Marr Chase"}], "over": [{"name": "2027 1st"}]},
            ],
            "league_ai_lessons": [{"league_id": "L1", "lesson": "Weight snap share over matchup.", "active": True, "evidence_count": 2, "created_at": t, "updated_at": t}],
            "league_ai_scorecards": [{"league_id": "L1", "season": 2026, "stats": {"by_kind": {"start_sit": "0-1 (0%)"}}}],
        })

    def test_report_totals_and_privacy(self):
        start, end = datetime(2026, 9, 29, tzinfo=timezone.utc), datetime(2026, 10, 6, tzinfo=timezone.utc)
        rep = build_report(self._db(), "L1", start, end)
        self.assertAlmostEqual(rep["cost"]["total"], 0.073)
        self.assertAlmostEqual(rep["cost"]["season_to_date"], 1.073)
        self.assertAlmostEqual(rep["cost"]["batch_savings"], 0.039)
        self.assertEqual(rep["usage"]["answers"], 2)
        self.assertEqual(rep["usage"]["per_owner"]["Tommy Bruggeman"]["calls_logged"], 1)
        self.assertEqual(rep["calls"]["graded"], 1)
        self.assertEqual(rep["calls"]["wrong"], 1)
        self.assertEqual(rep["calls"]["still_open"], 1)
        self.assertIn("trade evaluation (players hidden)", [c["call"] for c in rep["calls"]["logged_list"]])
        self.assertTrue(all(ch["questions"] is None and ch["title"] is None for ch in rep["chats"]))
        html = render_html(rep, "FLEG")
        self.assertIn("League AI weekly report", html)
        self.assertNotIn("Secret trade plan", html)
        self.assertNotIn("Tommy&#x27;s Chase", html)
        self.assertIn("Weight snap share over matchup.", html)

    def test_full_detail_shows_questions(self):
        rep = build_report(self._db(), "L1", datetime(2026, 9, 29, tzinfo=timezone.utc), datetime(2026, 10, 6, tzinfo=timezone.utc), detail="full")
        html = render_html(rep)
        self.assertIn("Should I trade for Tommy&#x27;s Chase?", html)
        self.assertIn("accept trade: get Ja&#x27;Marr Chase for 2027 1st", html)


if __name__ == "__main__":
    unittest.main()
