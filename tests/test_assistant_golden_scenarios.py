from __future__ import annotations

import time
import unittest

from gm_assistant.answer_packet import build_answer_packet_payload
from gm_assistant.assistant_pipeline import run_assistant_pipeline
from gm_assistant.conversation_state import ConversationState
from gm_assistant.rendered_answer import RenderedValidationStatus
from gm_assistant.request_context import AssistantRequestContext
from tests.fixtures.assistant_golden_scenarios import (
    COVERAGE_MATRIX,
    GoldenLookupClient,
    GoldenRetrievalProvider,
    LEAGUE_ID,
    OTHER_LEAGUE_ID,
    OTHER_LEAGUE_TEAM_ID,
    OWNER_PREFERENCES,
    TEAM_CONTEXT,
    TEAM_ID,
    USER_ID,
    golden_scenarios,
    make_context,
)


def run_scenario(scenario, *, context=None, provider=None):
    context = context or make_context()
    state = ConversationState(
        conversation_id=context.conversation_id or "conversation-golden",
        user_id=context.user_id,
        league_id=context.league_id,
        league_team_id=context.league_team_id,
        discussed_player_ids=list(scenario.prior_players),
        current_scenario=scenario.prior_scenario,
    )
    return run_assistant_pipeline(
        context=context,
        question=scenario.question,
        conversation_state=state,
        retrieval_provider=provider or GoldenRetrievalProvider(),
        rendered_text=scenario.rendered_text,
        owner_preferences=OWNER_PREFERENCES,
        team_context=TEAM_CONTEXT,
        interpreter_sb=GoldenLookupClient(),
    )


def divergence_message(scenario, result, expected, actual, stage):
    return (
        f"Scenario: {scenario.name}\n"
        f"Expected: {expected}\n"
        f"Actual: {actual}\n"
        f"First divergence: {stage}\n"
        f"Plan: {result.decision_plan.plan_type}\n"
        f"Evidence: {[item.retrieval_type + ':' + item.status for item in result.evidence_packet.retrieval_results]}\n"
        f"Decision: {result.decision_output.action}/{result.decision_output.recommendation_status}\n"
        f"Answer: {result.answer_packet.answer_mode}/{result.answer_packet.response_status}"
    )


class AssistantGoldenScenarioTest(unittest.TestCase):
    def test_golden_scenarios_cover_stage_1_to_12_pipeline(self):
        scenarios = golden_scenarios()
        self.assertGreaterEqual(len(scenarios), 20)
        for scenario in scenarios:
            with self.subTest(scenario=scenario.name):
                result = run_scenario(scenario)
                expectation = scenario.expectation
                checks = [
                    ("interpretation", expectation.interpreted_intent, result.interpreted_question.primary_intent),
                    ("planning", expectation.plan_type, result.decision_plan.plan_type),
                    ("rules", expectation.rule_status, result.rules_evaluation.overall_status),
                    ("decision", expectation.decision_action, result.decision_output.action),
                    ("validation", expectation.validation_status, result.recommendation_validation.validation_status),
                    ("answer_packet", expectation.answer_mode, result.answer_packet.answer_mode),
                    ("answer_packet", expectation.response_status, result.answer_packet.response_status),
                    ("validation", expectation.approved_for_action, result.answer_packet.approved_for_action),
                ]
                for stage, expected, actual in checks:
                    if expected is None:
                        continue
                    self.assertEqual(
                        expected,
                        actual,
                        divergence_message(scenario, result, expected, actual, stage),
                    )
                retrieval_types = {item.retrieval_type for item in result.evidence_packet.retrieval_results}
                for retrieval_type in expectation.required_evidence_types:
                    self.assertIn(
                        retrieval_type,
                        retrieval_types,
                        divergence_message(scenario, result, retrieval_type, sorted(retrieval_types), "evidence"),
                    )
                calculation_types = {item.calculation_type for item in result.calculation_packet.results}
                calculation_types.update(item.calculation_type for item in result.calculation_packet.unresolved_calculations)
                for calculation_type in expectation.required_calculation_types:
                    self.assertIn(
                        calculation_type,
                        calculation_types,
                        divergence_message(scenario, result, calculation_type, sorted(calculation_types), "calculations"),
                    )
                lower_answer = result.displayed_answer.lower()
                for concept in expectation.must_include_concepts:
                    self.assertIn(concept.lower(), lower_answer)
                for concept in expectation.must_exclude_concepts:
                    self.assertNotIn(concept.lower(), lower_answer)
                self.assertTrue(result.displayed_answer)
                self.assertNotIn("AnswerPacket", result.displayed_answer)

    def test_identity_scope_and_cross_league_isolation(self):
        context = make_context()
        result = run_scenario(golden_scenarios()[0], context=context)
        self.assertEqual(result.context.user_id, USER_ID)
        self.assertEqual(result.context.league_id, LEAGUE_ID)
        self.assertEqual(result.context.league_team_id, TEAM_ID)
        self.assertEqual(result.evidence_packet.request_context_ref.league_team_id, TEAM_ID)
        payload = repr(build_answer_packet_payload(result.answer_packet))
        self.assertNotIn(OTHER_LEAGUE_ID, payload)
        self.assertNotIn(OTHER_LEAGUE_TEAM_ID, payload)
        self.assertNotIn("Cross League Star", payload)

    def test_missing_team_scope_is_blocked_before_evidence(self):
        scenario = golden_scenarios()[0]
        bad_context = make_context(league_team_id="")
        provider = GoldenRetrievalProvider()
        result = run_scenario(scenario, context=bad_context, provider=provider)
        self.assertEqual(result.evidence_packet.execution_status, "blocked")
        self.assertEqual(provider.calls, [])

    def test_commissioner_cannot_use_stage_13_path_to_read_unrelated_team_context(self):
        scenario = golden_scenarios()[1]
        context = make_context(role="commissioner", permission_scopes=("team_advice", "league_public_read", "league_admin"))
        result = run_scenario(scenario, context=context)
        self.assertEqual(result.context.league_team_id, TEAM_ID)
        self.assertTrue(
            all(
                not request.team_ids or request.team_ids == [TEAM_ID]
                for request in result.decision_plan.retrieval_requests
                if request.scope == "team"
            )
        )

    def test_conversation_follow_up_and_topic_change(self):
        player_scenario = next(item for item in golden_scenarios() if item.name == "player_eval")
        first = run_scenario(player_scenario)
        self.assertIn("p-garrett", first.conversation_state.discussed_player_ids)

        follow_up = run_assistant_pipeline(
            context=first.context,
            question="What about his contract?",
            conversation_state=first.conversation_state,
            retrieval_provider=GoldenRetrievalProvider(),
            owner_preferences=OWNER_PREFERENCES,
            team_context=TEAM_CONTEXT,
            interpreter_sb=GoldenLookupClient(),
        )
        self.assertTrue(follow_up.interpreted_question.is_follow_up)
        self.assertEqual(follow_up.interpreted_question.follow_up_target, "player")

        topic_change = run_assistant_pipeline(
            context=first.context,
            question="Actually, how much cap space do I have?",
            conversation_state=follow_up.conversation_state,
            retrieval_provider=GoldenRetrievalProvider(),
            owner_preferences=OWNER_PREFERENCES,
            team_context=TEAM_CONTEXT,
            interpreter_sb=GoldenLookupClient(),
        )
        self.assertEqual(topic_change.interpreted_question.primary_intent, "salary_cap_question")
        self.assertEqual(topic_change.decision_plan.plan_type, "salary_cap_plan")

    def test_rendered_answer_acceptance_and_fallback_paths(self):
        scenario = next(item for item in golden_scenarios() if item.name == "cap_space")
        compliant = run_scenario(scenario)
        self.assertEqual(compliant.rendered_validation.validation_status, RenderedValidationStatus.APPROVED.value)
        self.assertTrue(compliant.rendered_validation.used_openai_response)

        reversed_result = run_assistant_pipeline(
            context=make_context(),
            question=scenario.question,
            conversation_state=ConversationState("conversation-golden", USER_ID, LEAGUE_ID, TEAM_ID),
            retrieval_provider=GoldenRetrievalProvider(),
            rendered_text="I would reject this and say there is only 99 available.",
            owner_preferences=OWNER_PREFERENCES,
            team_context=TEAM_CONTEXT,
            interpreter_sb=GoldenLookupClient(),
        )
        self.assertEqual(reversed_result.rendered_validation.validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertTrue(reversed_result.rendered_validation.used_deterministic_fallback)
        self.assertNotIn("99 available", reversed_result.displayed_answer)

    def test_prompt_size_privacy_and_performance_audit(self):
        start = time.perf_counter()
        result = run_scenario(next(item for item in golden_scenarios() if item.name == "trade_reject"))
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertLess(elapsed_ms, 750)
        self.assertGreater(result.prompt_size_audit["stage_1_to_10_combined"], 0)
        self.assertGreater(result.prompt_size_audit["AnswerPacket"], 0)
        self.assertGreaterEqual(
            result.prompt_size_audit["approx_final_context_with_answer_packet"],
            result.prompt_size_audit["AnswerPacket"],
        )
        rendered_payload = repr(result.rendered_validation)
        self.assertNotIn("OPENAI_API_KEY", rendered_payload)
        self.assertNotIn("token_hash", rendered_payload)
        self.assertNotIn("chain-of-thought", rendered_payload.lower())

    def test_coverage_matrix_mentions_every_scenario(self):
        scenario_names = {scenario.name for scenario in golden_scenarios()}
        self.assertEqual(scenario_names, set(COVERAGE_MATRIX))
        required_areas = {
            "identity",
            "conversation",
            "facts",
            "rules",
            "calculations",
            "decision",
            "validation",
            "answer_packet",
            "fallback",
            "scope",
        }
        covered = set().union(*COVERAGE_MATRIX.values())
        self.assertTrue(required_areas.issubset(covered))


if __name__ == "__main__":
    unittest.main()
