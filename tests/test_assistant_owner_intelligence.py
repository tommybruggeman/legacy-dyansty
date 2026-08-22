from __future__ import annotations

import unittest

from gm_assistant.assistant_pipeline import run_assistant_pipeline
from gm_assistant.conversation_state import ConversationState
from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.owner_intelligence import (
    InMemoryOwnerIntelligenceRepository,
    OwnerIntelligenceService,
    OwnerPreference,
    OwnerPreferenceCategory,
    OwnerPreferenceSource,
    normalize_feedback,
    normalize_owner_preferences_from_text,
)
from gm_assistant.owner_intelligence.models import ConfirmationState, OwnerMemoryScope, OwnerPreferenceStatus
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE
from tests.test_assistant_scenario_simulator import FakeClient


def context(**overrides):
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


def pref(value, *, user_id="user-1", league_id="league-1", team_id="team-1", category=OwnerPreferenceCategory.STRATEGIC_GOAL.value, scope=OwnerMemoryScope.TEAM.value, source=OwnerPreferenceSource.EXPLICIT_USER_STATEMENT.value, status=OwnerPreferenceStatus.ACTIVE.value, inferred=False):
    return OwnerPreference(
        user_id=user_id,
        league_id=league_id if scope != OwnerMemoryScope.USER_GLOBAL.value else None,
        league_team_id=team_id if scope in {OwnerMemoryScope.TEAM.value, OwnerMemoryScope.CONVERSATION.value} else None,
        scope=scope,
        category=category,
        normalized_value=value,
        source_type=source,
        explicit=not inferred,
        inferred=inferred,
        confidence_band="low" if inferred else "high",
        status=status,
    )


class OwnerIntelligenceTest(unittest.TestCase):
    def test_empty_valid_context_with_missing_persistence_adapter(self):
        result = OwnerIntelligenceService().get_context(context=context(), current_message="Who is on my team?")

        self.assertEqual(result.active_preferences, [])
        self.assertEqual(result.availability.durable_persistence, "deferred")
        self.assertEqual(result.availability.explicit_preferences, "empty")

    def test_explicit_user_global_communication_preference(self):
        prefs, warnings = normalize_owner_preferences_from_text("Keep answers concise.", context())

        self.assertEqual(warnings, [])
        self.assertEqual(prefs[0].category, "communication_style")
        self.assertEqual(prefs[0].normalized_value, "concise")
        self.assertEqual(prefs[0].scope, "user_global")

    def test_explicit_league_and_team_specific_preferences(self):
        ctx = OwnerIntelligenceService().get_context(
            context=context(),
            current_message="For this league, I am rebuilding. I want to prioritize draft picks.",
        )

        self.assertEqual(ctx.strategy_state.strategic_goal.value, "rebuild")
        self.assertEqual(ctx.strategy_state.strategic_goal.scope, "team")
        self.assertTrue(any(item.normalized_value == "prioritize_draft_picks" and item.scope == "league" for item in ctx.strategy_state.asset_preferences))

    def test_conversation_only_context_is_not_durable(self):
        ctx = OwnerIntelligenceService().get_context(
            context=context(),
            current_message="For this scenario, assume I am rebuilding.",
        )

        self.assertTrue(ctx.temporary_preferences)
        self.assertEqual(ctx.temporary_preferences[0].status, "conversation_only")
        self.assertEqual(ctx.strategy_state.conversation_only[0].normalized_value, "rebuild")

    def test_normalization_rejects_ambiguous_preference_language(self):
        for text in ("Maybe I should rebuild.", "I might trade my first.", "This team feels old."):
            with self.subTest(text=text):
                prefs, warnings = normalize_owner_preferences_from_text(text, context())
                self.assertEqual(prefs, [])
                self.assertIn("ambiguous_preference_not_promoted", warnings)

    def test_supported_preference_categories(self):
        text = "I want to compete this year. I prefer younger players. I prefer safer moves. Give me several options. Challenge my assumptions."
        ctx = OwnerIntelligenceService().get_context(context=context(), current_message=text)

        self.assertEqual(ctx.strategy_state.strategic_goal.value, "contend")
        self.assertEqual(ctx.strategy_state.risk_preference.risk_tolerance, "conservative")
        self.assertTrue(any(item.normalized_value == "prefers_younger_players" for item in ctx.strategy_state.asset_preferences))
        self.assertTrue(any(item.normalized_value == "several_options" for item in ctx.strategy_state.decision_preferences))
        self.assertTrue(any(item.normalized_value == "challenge_assumptions" for item in ctx.strategy_state.decision_preferences))

    def test_hard_constraint_and_confirmation_required(self):
        ctx = OwnerIntelligenceService().get_context(context=context(), current_message="Do not recommend trading my 2028 first.")

        self.assertEqual(ctx.strategy_state.hard_constraints[0].constraint_type, "do_not_trade_first_round_pick")
        self.assertEqual(ctx.strategy_state.hard_constraints[0].target, "2028 first")
        self.assertEqual(ctx.strategy_state.confirmation_required[0].confirmation_state, ConfirmationState.REQUIRED.value)

    def test_source_priority_current_instruction_overrides_durable_and_inferred(self):
        repo = InMemoryOwnerIntelligenceRepository([
            pref("contend"),
            pref("rebuild", source=OwnerPreferenceSource.REPEATED_BEHAVIOR_INFERENCE.value, inferred=True),
        ])

        ctx = OwnerIntelligenceService(repo).get_context(context=context(), current_message="For this scenario, assume I am rebuilding.")

        self.assertEqual(ctx.strategy_state.strategic_goal.value, "rebuild")
        self.assertTrue(ctx.strategy_state.conversation_only)
        self.assertIn("conflicting_strategic_goal", ctx.strategy_state.conflicts)

    def test_explicit_preference_outranks_inferred_tendency(self):
        repo = InMemoryOwnerIntelligenceRepository([
            pref("rebuild", source=OwnerPreferenceSource.REPEATED_BEHAVIOR_INFERENCE.value, inferred=True),
            pref("contend", source=OwnerPreferenceSource.EXPLICIT_USER_STATEMENT.value),
        ])

        ctx = OwnerIntelligenceService(repo).get_context(context=context())

        self.assertEqual(ctx.strategy_state.strategic_goal.value, "contend")

    def test_preference_supersession(self):
        repo = InMemoryOwnerIntelligenceRepository([pref("rebuild")])
        superseded = repo.supersede_preference(repo.preferences[0])
        ctx = OwnerIntelligenceService(repo).get_context(context=context())

        self.assertEqual(superseded.status, "superseded")
        self.assertEqual(ctx.active_preferences, [])
        self.assertEqual(len(ctx.superseded_preferences), 1)

    def test_cross_user_and_cross_league_isolation(self):
        repo = InMemoryOwnerIntelligenceRepository([
            pref("rebuild", user_id="user-2"),
            pref("contend", league_id="league-2"),
            pref("retool"),
        ])

        ctx = OwnerIntelligenceService(repo).get_context(context=context())

        self.assertEqual(ctx.strategy_state.strategic_goal.value, "retool")

    def test_team_scope_isolation(self):
        repo = InMemoryOwnerIntelligenceRepository([
            pref("rebuild", team_id="team-2"),
            pref("contend", team_id="team-1"),
        ])

        ctx = OwnerIntelligenceService(repo).get_context(context=context())

        self.assertEqual(ctx.strategy_state.strategic_goal.value, "contend")

    def test_feedback_and_correctness_dispute_are_separate_and_do_not_mutate_facts(self):
        ctx = context()
        feedback = normalize_feedback("That was too aggressive.", ctx)
        dispute = normalize_feedback("That salary is wrong.", ctx)

        self.assertEqual(feedback.feedback_type, "too_aggressive")
        self.assertTrue(feedback.creates_preference_candidate)
        self.assertEqual(dispute.disputed_claim, "That salary is wrong.")
        self.assertFalse(dispute.factual_repository_mutation_allowed)

    def test_pipeline_consumes_owner_context_for_rebuild_and_contender_goals(self):
        rebuild = run_pipeline("Build me a three-year plan.", owner_preferences={"team_build_preference": "rebuild"})
        contend = run_pipeline("Build me a three-year plan.", owner_preferences={"team_build_preference": "contend"})

        self.assertEqual(rebuild.owner_intelligence_context.strategy_state.strategic_goal.value, "rebuild")
        self.assertEqual(contend.owner_intelligence_context.strategy_state.strategic_goal.value, "contend")
        self.assertEqual(rebuild.owner_objective.request_goal, "rebuild")
        self.assertEqual(contend.owner_objective.request_goal, "contend_this_season")

    def test_facts_and_legality_still_outrank_preferences(self):
        result = run_pipeline("How much cap space do I have?", owner_preferences={"team_build_preference": "rebuild"})

        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertIn("cap", result.displayed_answer.lower())
        self.assertIsNone(result.answer_packet.recommendation)

    def test_protected_pick_conflict_surfaces_but_scenario_still_runs(self):
        result = run_pipeline(
            "What happens if I trade my 2028 first?",
            owner_preferences={"notes": ["explicit_preference:do_not_trade_first_round_pick"]},
        )

        self.assertEqual(result.interpreted_question.primary_intent, "scenario_simulation")
        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertIn("moves out", result.displayed_answer)
        self.assertIn("conflicts with your explicit preference", result.displayed_answer)

    def test_scenario_calculation_invariant_under_strategy_preference(self):
        plain = run_pipeline("What happens if I cut Garrett Wilson?")
        rebuild = run_pipeline("What happens if I cut Garrett Wilson?", owner_preferences={"team_build_preference": "rebuild"})

        self.assertEqual(plain.evidence_packet.transaction_evidence[0].summary, rebuild.evidence_packet.transaction_evidence[0].summary)

    def test_missing_owner_context_does_not_break_scenario_simulator(self):
        result = run_pipeline("What happens if I cut Garrett Wilson?", owner_preferences={})

        self.assertIn("Garrett Wilson", result.displayed_answer)
        self.assertEqual(result.owner_intelligence_context.availability.explicit_preferences, "empty")

    def test_current_request_overrides_concise_and_detailed_preferences(self):
        concise = OwnerIntelligenceService().get_context(context=context(), current_message="Keep answers concise.")
        detailed = OwnerIntelligenceService().get_context(context=context(), current_message="Today, give me a detailed answer.")

        self.assertEqual(concise.strategy_state.communication_preference.style, "concise")
        self.assertEqual(detailed.strategy_state.communication_preference.style, "detailed")
        self.assertEqual(detailed.strategy_state.conversation_only[0].status, "conversation_only")

    def test_regression_factual_answers_remain_available(self):
        for question in (
            "Who is on my team?",
            "How much cap space do I have?",
            "What are my best and worst contracts?",
            "Who should I draft with the second overall rookie draft pick?",
            "What happens if I cut Garrett Wilson?",
        ):
            with self.subTest(question=question):
                result = run_pipeline(question)
                self.assertTrue(result.displayed_answer)


def run_pipeline(question: str, *, owner_preferences=None):
    client = FakeClient()
    return run_assistant_pipeline(
        context=context(),
        question=question,
        retrieval_provider=SupabaseEvidenceRetrievalProvider(client),
        interpreter_sb=client,
        owner_preferences=owner_preferences or {},
        team_context={},
    )


if __name__ == "__main__":
    unittest.main()
