from __future__ import annotations

import os
import unittest

from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.openai_reasoning import (
    FakeReasoningProvider,
    ReasoningResponse,
    UnavailableReasoningProvider,
    configuration_status,
    live_smoke_permitted,
)
from gm_assistant.runtime import AssistantRuntime, AssistantRuntimeInput
from tests.test_assistant_football_intelligence import FakeClient, context


def _response(**overrides):
    data = {
        "answer_type": "factual_explanation",
        "direct_answer": "Verified roster construction points to RB and TE as the clearest priorities.",
        "recommendation": None,
        "recommendation_strength": "none",
        "key_reasons": ["The deterministic roster context supplied those needs."],
        "main_risks": [],
        "alternatives": [],
        "clarifying_question": None,
        "facts_used": ["answer.direct_answer"],
        "limitations": ["Only verified local data was supplied."],
        "constraint_conflicts": [],
        "requires_deterministic_follow_up": False,
    }
    data.update(overrides)
    return ReasoningResponse(**data)


def _client_with_membership() -> FakeClient:
    client = FakeClient()
    client.rows["league_memberships"] = [
        {
            "id": "membership-1",
            "user_id": "user-1",
            "league_id": "league-1",
            "league_team_id": "team-1",
            "team_id": None,
            "role": "owner",
        }
    ]
    return client


class EnvPatch:
    def __init__(self, **values):
        self.values = values
        self.old = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.old[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)

    def __exit__(self, *_args):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class OpenAIActivationTest(unittest.TestCase):
    def test_configuration_diagnostics_are_safe_and_stable_without_key(self):
        with EnvPatch(OPENAI_API_KEY=None, OPENAI_REASONING_ENABLED="true", LEGACY_OPENAI_LIVE_TEST=None):
            status = configuration_status()

        self.assertFalse(status.api_key_present)
        self.assertFalse(status.configuration_valid)
        self.assertEqual(status.safe_error_code, "missing_api_key")
        self.assertFalse(status.live_testing_permitted)
        self.assertNotIn("sk-", repr(status.to_payload()))

    def test_reasoning_kill_switch_disables_live_smoke_and_selects_unavailable_provider(self):
        with EnvPatch(OPENAI_API_KEY="sk-placeholder", OPENAI_REASONING_ENABLED="false", LEGACY_OPENAI_LIVE_TEST="1"):
            status = configuration_status()
            permitted = live_smoke_permitted()

        self.assertFalse(status.reasoning_enabled)
        self.assertFalse(status.configuration_valid)
        self.assertEqual(status.safe_error_code, "reasoning_disabled")
        self.assertFalse(permitted)

    def test_secret_example_is_placeholder_only_and_real_secret_path_is_ignored(self):
        root = os.getcwd()
        with open(os.path.join(root, ".gitignore"), "r", encoding="utf-8") as fh:
            gitignore = fh.read()
        with open(os.path.join(root, ".streamlit", "secrets.example.toml"), "r", encoding="utf-8") as fh:
            example = fh.read()

        self.assertIn(".streamlit/secrets.toml", gitignore)
        self.assertIn("replace-with-local-key", example)
        self.assertNotIn("sk-", example)

    def test_runtime_calls_fake_provider_once_for_reasoning_eligible_question(self):
        provider = FakeReasoningProvider(_response())
        result = AssistantRuntime().run(
            AssistantRuntimeInput(
                context=context(),
                question="What should I prioritize this offseason?",
                supabase_client=_client_with_membership(),
                reasoning_provider=provider,
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(result.answer_text.strip())
        self.assertNotIn("sk-", repr(result.safe_metadata))
        self.assertTrue(result.safe_metadata["reasoning"]["provider_called"])
        self.assertEqual(result.safe_metadata["reasoning"]["provider_selected"], "Fake")

    def test_runtime_bypasses_provider_for_direct_factual_question(self):
        provider = FakeReasoningProvider(_response())
        result = AssistantRuntime().run(
            AssistantRuntimeInput(
                context=context(),
                question="Who is on my team?",
                supabase_client=_client_with_membership(),
                reasoning_provider=provider,
            )
        )

        self.assertTrue(result.ok)
        self.assertEqual(provider.calls, [])
        self.assertIn("Josh Allen", result.answer_text)
        self.assertFalse(result.safe_metadata["reasoning"]["provider_called"])

    def test_runtime_invalid_scope_blocks_before_provider_call(self):
        provider = FakeReasoningProvider(_response())
        result = AssistantRuntime().run(
            AssistantRuntimeInput(
                context=context(league_team_id=""),
                question="Should I make this trade?",
                supabase_client=_client_with_membership(),
                reasoning_provider=provider,
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "team_context_missing")
        self.assertEqual(provider.calls, [])

    def test_runtime_falls_back_for_missing_key_timeout_malformed_refusal_and_empty(self):
        cases = [
            UnavailableReasoningProvider("missing_api_key"),
            FakeReasoningProvider(TimeoutError("raw timeout body"), error_code="timeout"),
            FakeReasoningProvider(_response(facts_used=["unknown.ref"])),
            FakeReasoningProvider(_response(direct_answer="I cannot reveal an API key.", facts_used=["answer.direct_answer"])),
            FakeReasoningProvider(_response(direct_answer="", facts_used=["answer.direct_answer"])),
        ]

        for provider in cases:
            with self.subTest(provider=type(provider).__name__, code=getattr(provider, "reason_code", None)):
                result = AssistantRuntime().run(
                    AssistantRuntimeInput(
                        context=context(),
                        question="What should I prioritize this offseason?",
                        supabase_client=_client_with_membership(),
                        reasoning_provider=provider,
                    )
                )
                self.assertTrue(result.ok)
                self.assertNotIn("raw timeout body", result.answer_text)
                self.assertNotIn("api key", result.answer_text.lower())


if __name__ == "__main__":
    unittest.main()
