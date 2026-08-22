from __future__ import annotations

import unittest

from gm_assistant.evaluation import (
    initial_evaluation_cases,
    render_markdown_report,
    run_evaluation_suite,
    score_evaluation_case,
    synthetic_qb_shortage_request,
)
from gm_assistant.openai_reasoning import FakeReasoningProvider, ReasoningResponse


def _response(**overrides):
    data = {
        "answer_type": "recommendation",
        "direct_answer": "Use the verified roster facts to avoid creating a QB depth shortage.",
        "recommendation": "Do not include the backup quarterback unless another verified quarterback path is available.",
        "recommendation_strength": "moderate",
        "key_reasons": ["The supplied football intelligence marks QB depth as a shortage risk."],
        "main_risks": ["The protected future first reduces flexibility if added unnecessarily."],
        "alternatives": [],
        "clarifying_question": None,
        "facts_used": ["answer.direct_answer", "owner.goal"],
        "limitations": ["No counterparty acceptance evidence was supplied."],
        "constraint_conflicts": [],
        "requires_deterministic_follow_up": False,
    }
    data.update(overrides)
    return ReasoningResponse(**data)


class OpenAIEvaluationTest(unittest.TestCase):
    def test_initial_suite_has_required_coverage(self):
        cases = initial_evaluation_cases()
        categories = {case.category for case in cases}

        self.assertGreaterEqual(len(cases), 20)
        self.assertIn("direct_factual_bypass", categories)
        self.assertIn("trade_reasoning", categories)
        self.assertIn("roster_strategy", categories)
        self.assertIn("draft_strategy", categories)
        self.assertIn("league_context", categories)
        self.assertIn("safety", categories)

    def test_synthetic_live_smoke_fixture_is_scoped_and_non_production(self):
        request = synthetic_qb_shortage_request()

        self.assertEqual(request.league_id, "synthetic-league")
        self.assertEqual(request.league_team_id, "synthetic-team")
        self.assertIn("protected", repr(request.to_payload()).lower())
        self.assertNotIn("9838a0a1", repr(request.to_payload()))

    def test_grader_blocks_forbidden_claims_secrets_and_internal_language(self):
        case = initial_evaluation_cases()[6]

        forbidden = score_evaluation_case(
            case,
            rendered_answer="The other owner will accept and the API key is sk-test.",
            provider_called=True,
            validation_status="approved",
            fallback_status="openai_reasoning_used",
        )
        internal = score_evaluation_case(
            case,
            rendered_answer="The validated recommendation is to not applicable.",
            provider_called=True,
            validation_status="approved",
            fallback_status="openai_reasoning_used",
        )

        self.assertFalse(forbidden.passed)
        self.assertFalse(internal.passed)
        self.assertIn("fail", (forbidden.safety, internal.safety))

    def test_evaluation_runner_preserves_bypass_and_one_call_per_eligible_case(self):
        provider = FakeReasoningProvider(_response())
        suite = run_evaluation_suite(provider=provider)
        eligible_count = sum(1 for case in initial_evaluation_cases() if case.openai_eligible)

        self.assertEqual(suite.total_cases, 24)
        self.assertEqual(len(provider.calls), eligible_count)
        self.assertTrue(all(result.provider_call_count <= 1 for result in suite.results))
        self.assertTrue(all(not result.provider_called for result in suite.results if not result.case.openai_eligible))
        self.assertEqual(suite.safety_failures, 0)

    def test_evaluation_runner_records_fallback_for_rejected_provider_response(self):
        provider = FakeReasoningProvider(_response(facts_used=["unknown.ref"]))
        suite = run_evaluation_suite(provider=provider, cases=(initial_evaluation_cases()[6],))
        result = suite.results[0]

        self.assertTrue(result.provider_called)
        self.assertEqual(result.validation_status, "rejected")
        self.assertEqual(result.fallback_status, "deterministic_fallback")
        self.assertIn(result.comparison, {"neutral", "regressed"})

    def test_markdown_report_is_human_reviewable_and_secret_safe(self):
        suite = run_evaluation_suite(provider=FakeReasoningProvider(_response()), cases=initial_evaluation_cases()[:3])
        report = render_markdown_report(suite)

        self.assertIn("Legacy GM Assistant OpenAI Evaluation", report)
        self.assertIn("Reviewer notes", report)
        self.assertNotIn("sk-", report)
        self.assertNotIn("OPENAI_API_KEY", report)
        self.assertNotIn("9838a0a1", report)


if __name__ == "__main__":
    unittest.main()
