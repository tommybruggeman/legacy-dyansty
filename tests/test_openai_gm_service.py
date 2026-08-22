from __future__ import annotations

import importlib
import os
import unittest


service = importlib.import_module("gm_assistant.openai_service")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name, action="select", payload=None, on_conflict=None):
        self.client = client
        self.table_name = table_name
        self.action = action
        self.payload = payload
        self.on_conflict = on_conflict
        self.filters = []
        self.limit_value = None

    def select(self, _cols="*"):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        if self.client.fail_table == self.table_name:
            raise RuntimeError("database unavailable")
        if self.action == "upsert":
            self.client.upserts.append((self.table_name, self.payload, self.on_conflict))
            return Result([self.payload])

        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            rows = [row for row in rows if str(row.get(key)) == str(value)]
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return Result(rows)


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, cols="*"):
        return FakeQuery(self.client, self.table_name).select(cols)

    def upsert(self, payload, on_conflict=None):
        return FakeQuery(self.client, self.table_name, action="upsert", payload=payload, on_conflict=on_conflict)


class FakeSupabase:
    def __init__(self):
        self.fail_table = None
        self.upserts = []
        self.rows = {
            "league_memberships": [
                {
                    "id": "membership-1",
                    "user_id": "user-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_id": None,
                    "role": "owner",
                }
            ],
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Tommy Bruggeman", "owner_name": "Tommy Bruggeman"},
                {"id": "team-2", "league_id": "league-1", "team_name": "Dylan Burruel", "owner_name": "Dylan Burruel"},
                {"id": "team-x", "league_id": "league-x", "team_name": "Other League", "owner_name": "Other League"},
            ],
            "team_brain": [
                {
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_name": "Tommy Bruggeman",
                    "team_direction": "CONTEND_NOW",
                    "position_strengths": ["QB", "WR"],
                    "position_needs": ["RB"],
                    "core_players": ["Josh Allen", "Garrett Wilson"],
                    "contract_problems": ["Expensive WR"],
                    "championship_window_score": 88,
                },
                {
                    "league_id": "league-1",
                    "league_team_id": "team-2",
                    "team_name": "Dylan Burruel",
                    "team_direction": "RETOOL",
                    "position_strengths": ["RB"],
                    "position_needs": ["QB"],
                    "core_players": ["Bijan Robinson"],
                    "championship_window_score": 71,
                },
                {
                    "league_id": "league-x",
                    "league_team_id": "team-x",
                    "team_name": "Other League",
                    "championship_window_score": 99,
                },
            ],
            "league_brain": [
                {
                    "league_id": "league-1",
                    "summary": "League one summary.",
                    "trade_fits": [{"team_a": "Tommy Bruggeman", "team_b": "Dylan Burruel"}],
                }
            ],
            "player_strategic_profiles": [
                {
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "owner_team_name": "Tommy Bruggeman",
                    "player_name": "Garrett Wilson",
                    "sleeper_id": "p1",
                    "position": "WR",
                    "salary": 24,
                    "contract_flag": "OK",
                    "strategic_label": "CORE",
                }
            ],
            "league_relative_player_values": [
                {
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "player_name": "Garrett Wilson",
                    "sleeper_id": "p1",
                    "league_value_tier": "TOP_STARTER",
                    "overall_percentile": 91,
                }
            ],
            "contracts": [
                {
                    "league_id": "league-1",
                    "owner_name": "Tommy Bruggeman",
                    "player_name": "Garrett Wilson",
                    "player_position": "WR",
                    "salary": 24,
                    "contract_years_left": 2,
                    "contract_total_years": 4,
                    "sleeper_player_id": "p1",
                }
            ],
            "v_team_caps": [
                {"league_id": "league-1", "owner_name": "Tommy Bruggeman", "cap_space": 12, "total_salary": 213}
            ],
            "transactions_enriched": [
                {"league_id": "league-1", "type": "trade", "description": "Trade logged."}
            ],
            "gm_user_memory": [],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


class FakeResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.responses[0], BaseException):
            raise self.responses.pop(0)
        return self.responses.pop(0)


class FakeOpenAI:
    def __init__(self, responses):
        self.responses = FakeResponses(responses)


def final_response(text, response_id="resp-final"):
    return {
        "id": response_id,
        "_request_id": f"req-{response_id}",
        "output_text": text,
        "output": [],
    }


def tool_response(name, arguments=None, call_id="call-1", response_id="resp-tool"):
    return {
        "id": response_id,
        "_request_id": f"req-{response_id}",
        "output": [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments or "{}",
            }
        ],
    }


class OpenAIGMServiceTest(unittest.TestCase):
    def setUp(self):
        self.sb = FakeSupabase()
        self.identity = service.AssistantIdentity(
            team_name="Tommy Bruggeman",
            user_id="user-1",
            league_id="league-1",
            league_team_id="team-1",
        )

    def test_direct_final_response_and_raw_question(self):
        client = FakeOpenAI([final_response("You are Tommy Bruggeman.")])

        answer = service.answer_gm_question(
            "Who am I?",
            self.identity,
            conversation_history=[],
            sb=self.sb,
            client=client,
        )

        self.assertEqual(answer.text, "You are Tommy Bruggeman.")
        self.assertEqual(answer.tool_calls, [])
        self.assertEqual(client.responses.calls[0]["input"][-1]["content"], "Who am I?")

    def test_one_tool_call_returns_identity_context(self):
        client = FakeOpenAI([
            tool_response("get_current_user_context", "{}"),
            final_response("You are the owner of Tommy Bruggeman."),
        ])

        answer = service.answer_gm_question("Who am I?", self.identity, sb=self.sb, client=client)

        self.assertEqual(answer.tool_calls, ["get_current_user_context"])
        tool_output = client.responses.calls[1]["input"][0]["output"]
        self.assertIn("Tommy Bruggeman", tool_output)
        self.assertEqual(answer.text, "You are the owner of Tommy Bruggeman.")

    def test_multiple_sequential_tool_calls(self):
        client = FakeOpenAI([
            tool_response("get_my_team_brain", "{}"),
            tool_response("get_league_team_rankings", "{}"),
            final_response("You are a contender, and these are the strongest teams."),
        ])

        answer = service.answer_gm_question("Should I rebuild or contend?", self.identity, sb=self.sb, client=client)

        self.assertEqual(answer.tool_calls, ["get_my_team_brain", "get_league_team_rankings"])
        self.assertEqual(len(client.responses.calls), 3)

    def test_unknown_tool_is_returned_as_safe_tool_error(self):
        result = service.execute_assistant_tool("drop_everything", {}, identity=self.identity, sb=self.sb)

        self.assertEqual(result["error"], "unknown_tool")

    def test_malformed_arguments_are_rejected(self):
        result = service.execute_assistant_tool("get_player_contract", {}, identity=self.identity, sb=self.sb)

        self.assertEqual(result["error"], "malformed_arguments")

    def test_tool_execution_failure_is_sanitized(self):
        self.sb.fail_table = "contracts"

        result = service.execute_assistant_tool(
            "get_player_contract",
            {"player_name": "Garrett Wilson"},
            identity=self.identity,
            sb=self.sb,
        )

        self.assertEqual(result["error"], "tool_execution_failed")

    def test_api_timeout_returns_friendly_error(self):
        class FakeTimeout(Exception):
            pass

        old_timeout = service.APITimeoutError
        service.APITimeoutError = FakeTimeout
        try:
            client = FakeOpenAI([FakeTimeout("slow")])
            with self.assertRaises(service.AssistantServiceError) as ctx:
                service.answer_gm_question("Summarize my team in five bullets.", self.identity, sb=self.sb, client=client)
        finally:
            service.APITimeoutError = old_timeout

        self.assertIn("timed out", str(ctx.exception))

    def test_api_key_missing_is_stable_configuration_error(self):
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(service.AssistantConfigurationError):
                service.answer_gm_question("Who am I?", self.identity, sb=self.sb)
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key

    def test_max_tool_loop_protection(self):
        client = FakeOpenAI([
            tool_response("get_my_team_brain", "{}", response_id="r1"),
            tool_response("get_my_team_brain", "{}", response_id="r2"),
        ])

        with self.assertRaises(service.AssistantServiceError) as ctx:
            service.answer_gm_question(
                "Keep looking forever.",
                self.identity,
                sb=self.sb,
                client=client,
                max_tool_rounds=0,
            )

        self.assertIn("too many data lookups", str(ctx.exception))

    def test_cross_league_tool_arguments_are_rejected(self):
        result = service.execute_assistant_tool(
            "get_league_brain",
            {"league_id": "league-x"},
            identity=self.identity,
            sb=self.sb,
        )

        self.assertEqual(result["error"], "access_denied")

    def test_conversation_history_is_scoped_to_supplied_history(self):
        client = FakeOpenAI([final_response("Different question, different answer.")])

        service.answer_gm_question(
            "What is Garrett Wilson's contract?",
            self.identity,
            conversation_history=[
                {"role": "user", "content": "Only this user's previous question."},
                {"role": "assistant", "content": "Only this user's previous answer."},
            ],
            sb=self.sb,
            client=client,
        )

        sent = client.responses.calls[0]["input"]
        self.assertIn("Only this user's previous question.", sent[0]["content"])
        self.assertNotIn("other league", str(sent).lower())

    def test_different_questions_can_route_differently(self):
        client_one = FakeOpenAI([
            tool_response("get_player_contract", '{"player_name":"Garrett Wilson"}'),
            final_response("Garrett Wilson has a contract row."),
        ])
        client_two = FakeOpenAI([
            tool_response("get_league_team_rankings", "{}"),
            final_response("The strongest teams are ranked by team brain."),
        ])

        first = service.answer_gm_question("What is Garrett Wilson's contract?", self.identity, sb=self.sb, client=client_one)
        second = service.answer_gm_question("Who are the strongest teams?", self.identity, sb=self.sb, client=client_two)

        self.assertEqual(first.tool_calls, ["get_player_contract"])
        self.assertEqual(second.tool_calls, ["get_league_team_rankings"])
        self.assertNotEqual(first.text, second.text)

    def test_missing_data_can_be_reported_honestly(self):
        result = service.execute_assistant_tool(
            "get_player_contract",
            {"player_name": "Missing Player"},
            identity=self.identity,
            sb=self.sb,
        )

        self.assertEqual(result["contracts"], [])

    def test_five_bullet_eval_case_uses_model_answer_without_canned_plan(self):
        text = "\n".join([
            "- QB is a strength.",
            "- WR has core value.",
            "- RB is the pressure point.",
            "- Cap space is limited.",
            "- Contention window is open.",
        ])
        client = FakeOpenAI([tool_response("get_my_team_brain", "{}"), final_response(text)])

        answer = service.answer_gm_question("Summarize my team in five bullets.", self.identity, sb=self.sb, client=client)

        bullets = [line for line in answer.text.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), 5)
        self.assertNotIn("My 3-step plan", answer.text)


if __name__ == "__main__":
    unittest.main()
