from __future__ import annotations

import inspect
import sys
import types
import unittest
from types import SimpleNamespace


auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

from gm_assistant.conversation_state import ConversationState
from gm_assistant.openai_service import AssistantAnswer, AssistantServiceError
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE
from gm_assistant import runtime as runtime_mod


def make_context(**overrides):
    data = {
        "user_id": "user-1",
        "league_id": "league-1",
        "league_team_id": "team-1",
        "membership_id": "membership-1",
        "role": "owner",
        "current_season": 2026,
        "requested_season": 2026,
        "permission_scopes": (TEAM_ADVICE, LEAGUE_PUBLIC_READ),
        "conversation_id": "conversation-1",
        "team_name": "Condor Dynasty",
        "owner_name": "Owner One",
    }
    data.update(overrides)
    return AssistantRequestContext(**data)


def fake_pipeline_result(context, state):
    return SimpleNamespace(
        context=context,
        conversation_state=state,
        interpreted_question=SimpleNamespace(primary_intent="data_lookup"),
        owner_objective=SimpleNamespace(),
        decision_plan=SimpleNamespace(),
        evidence_packet=SimpleNamespace(secret_raw_evidence="not-for-ui"),
        rules_evaluation=SimpleNamespace(),
        calculation_packet=SimpleNamespace(),
        decision_output=SimpleNamespace(),
        recommendation_validation=SimpleNamespace(validation_status="not_applicable"),
        answer_packet=SimpleNamespace(answer_mode="direct_fact"),
        rendered_validation=SimpleNamespace(validation_status="fallback_used"),
        displayed_answer="Pipeline fallback",
        prompt_size_audit={"AnswerPacket": 10},
    )


class AssistantRuntimeTest(unittest.TestCase):
    def test_runtime_invokes_pipeline_before_answerer_and_returns_stable_result(self):
        calls = []
        context = make_context()
        state = ConversationState("conversation-1", "user-1", "league-1", "team-1")

        def fake_run_pipeline(**kwargs):
            calls.append("pipeline")
            self.assertEqual(kwargs["context"], context)
            self.assertEqual(kwargs["conversation_state"], state)
            self.assertEqual(kwargs["interpreter_sb"], "sb")
            return fake_pipeline_result(context, state)

        def fake_answerer(**kwargs):
            calls.append("answerer")
            self.assertEqual(kwargs["identity"].user_id, "user-1")
            self.assertEqual(kwargs["identity"].league_id, "league-1")
            self.assertEqual(kwargs["identity"].league_team_id, "team-1")
            self.assertEqual(kwargs["answer_packet"].answer_mode, "direct_fact")
            return AssistantAnswer(
                text="You manage Condor Dynasty.",
                model="test-model",
                tool_calls=[],
                request_ids=[],
                latency_ms=1,
                rendered_validation=SimpleNamespace(validation_status="approved"),
            )

        original = runtime_mod.run_assistant_pipeline
        runtime_mod.run_assistant_pipeline = fake_run_pipeline
        try:
            result = runtime_mod.AssistantRuntime(
                answerer=fake_answerer,
                retrieval_provider_factory=lambda sb: ("provider", sb),
            ).run(
                runtime_mod.AssistantRuntimeInput(
                    context=context,
                    question="What team do I manage?",
                    conversation_state=state,
                    conversation_history=[{"role": "assistant", "content": "Hi"}],
                    owner_preferences={"gm_style": "balanced"},
                    team_context={"team_brain": {}},
                    supabase_client="sb",
                )
            )
        finally:
            runtime_mod.run_assistant_pipeline = original

        self.assertEqual(calls, ["pipeline", "answerer"])
        self.assertTrue(result.ok)
        self.assertEqual(result.answer_text, "You manage Condor Dynasty.")
        self.assertEqual(result.conversation_state, state)
        self.assertEqual(result.validation_status, "not_applicable")
        self.assertEqual(result.rendered_validation_status, "approved")
        self.assertEqual(result.safe_metadata["model"], "test-model")
        self.assertFalse(hasattr(result, "evidence_packet"))
        self.assertFalse(hasattr(result, "decision_plan"))
        self.assertIn("evidence", result.safe_metadata)
        self.assertEqual(result.safe_metadata["evidence"]["evaluated_player_count"], 0)
        self.assertFalse(result.safe_metadata["evidence"]["player_evaluation_requested"])

    def test_runtime_does_not_import_streamlit(self):
        source_lines = inspect.getsource(runtime_mod).splitlines()

        self.assertFalse(any(line.startswith("import streamlit") for line in source_lines))
        self.assertFalse(any(line.startswith("from streamlit") for line in source_lines))

    def test_runtime_blocks_missing_identity_before_pipeline(self):
        calls = []

        def fake_answerer(**_kwargs):
            calls.append("answerer")
            raise AssertionError("answerer should not be called")

        result = runtime_mod.AssistantRuntime(answerer=fake_answerer).run(
            runtime_mod.AssistantRuntimeInput(
                context=make_context(user_id=""),
                question="Who is on my team?",
                supabase_client="sb",
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "identity_context_missing")
        self.assertEqual(calls, [])

    def test_runtime_blocks_missing_team_scope_before_pipeline(self):
        result = runtime_mod.AssistantRuntime().run(
            runtime_mod.AssistantRuntimeInput(
                context=make_context(league_team_id=""),
                question="Who is on my team?",
                supabase_client="sb",
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "team_context_missing")

    def test_runtime_maps_answer_service_failure_to_bounded_failure(self):
        context = make_context()
        state = ConversationState("conversation-1", "user-1", "league-1", "team-1")

        def fake_run_pipeline(**_kwargs):
            return fake_pipeline_result(context, state)

        def failing_answerer(**_kwargs):
            raise AssistantServiceError("raw provider failure")

        original = runtime_mod.run_assistant_pipeline
        runtime_mod.run_assistant_pipeline = fake_run_pipeline
        try:
            result = runtime_mod.AssistantRuntime(answerer=failing_answerer).run(
                runtime_mod.AssistantRuntimeInput(
                    context=context,
                    question="Who is on my team?",
                    supabase_client="sb",
                )
            )
        finally:
            runtime_mod.run_assistant_pipeline = original

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "answer_service_unavailable")
        self.assertNotIn("raw provider failure", result.answer_text)


if __name__ == "__main__":
    unittest.main()
