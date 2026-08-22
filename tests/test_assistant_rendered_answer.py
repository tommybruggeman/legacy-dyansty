from __future__ import annotations

import importlib
import sys
import types
import unittest
from dataclasses import replace


auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

from gm_assistant.answer_packet import AnswerClaim, AnswerCondition, AnswerLimitation, AnswerMode, AnswerPacket, AnswerRecommendation, AnswerWarning, ResponseStatus
from gm_assistant.openai_reasoning import FakeReasoningProvider, ReasoningResponse
from gm_assistant.rendered_answer import (
    RenderedValidationStatus,
    build_rendered_answer_validation_payload,
    render_answer_packet_fallback,
    validate_rendered_answer,
)
from gm_assistant.validation import RecommendationValidation, ValidationStatus
from tests.test_assistant_answer_packet import packet
from tests.test_assistant_validation import calc_packet, calc_request, decision, evidence, interpreted, objective, plan, result, rules, validate
from tests.test_openai_gm_service import FakeOpenAI, FakeSupabase, final_response


def approved_trade_packet(**overrides):
    base = packet()
    data = {
        "answer_mode": base.answer_mode,
        "response_status": base.response_status,
        "direct_answer": base.direct_answer,
        "recommendation": base.recommendation,
        "facts": base.facts,
        "rule_conclusions": base.rule_conclusions,
        "calculations": base.calculations,
        "reasons": base.reasons,
        "conditions": base.conditions,
        "warnings": base.warnings,
        "limitations": base.limitations,
        "alternatives": base.alternatives,
        "forbidden_claims": base.forbidden_claims,
        "source_index": base.source_index,
        "audit_events": base.audit_events,
        "approved_for_explanation": base.approved_for_explanation,
        "approved_for_action": base.approved_for_action,
        "clarification_required": base.clarification_required,
        "reduced_mode": base.reduced_mode,
        "confidence": base.confidence,
    }
    data.update(overrides)
    return AnswerPacket(**data)


def limited_packet():
    validation = RecommendationValidation(ValidationStatus.REJECTED.value, False, False, decision_action="accept", recommendation_status="recommended")
    return packet(validation=validation)


class RenderedAnswerValidationTest(unittest.TestCase):
    def test_presence_valid_empty_whitespace_none_placeholder_metadata(self):
        answer = approved_trade_packet()
        self.assertEqual(validate_rendered_answer(answer, "My recommendation: accept Accept.").validation_status, RenderedValidationStatus.APPROVED.value)
        for text in ("", "   ", None, "{}", "answer_mode: recommendation"):
            with self.subTest(text=text):
                validation = validate_rendered_answer(answer, text)
                self.assertEqual(validation.validation_status, RenderedValidationStatus.FALLBACK_USED.value)
                self.assertTrue(validation.used_deterministic_fallback)

    def test_modes_validate_fact_rules_recommendation_limited_clarification_unsupported_blocked_failed(self):
        factual = approved_trade_packet(
            answer_mode=AnswerMode.DIRECT_FACT.value,
            response_status=ResponseStatus.READY.value,
            direct_answer=AnswerClaim("factual", "Your verified available cap is 12 for the requested season.", "ready", ["fact"], "high"),
            recommendation=None,
        )
        self.assertTrue(validate_rendered_answer(factual, "Your verified available cap is 12 for the requested season.").used_openai_response)

        rules_packet = approved_trade_packet(
            answer_mode=AnswerMode.DIRECT_RULES.value,
            direct_answer=AnswerClaim("legal", "The deterministic rules result is illegal.", "ready", ["rules"], "high"),
            recommendation=None,
        )
        self.assertTrue(validate_rendered_answer(rules_packet, "The rules result is illegal.").used_openai_response)

        self.assertTrue(validate_rendered_answer(approved_trade_packet(), "My recommendation: accept Accept.").used_openai_response)
        self.assertEqual(validate_rendered_answer(limited_packet(), "You should accept this trade.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

        clarification = approved_trade_packet(
            answer_mode=AnswerMode.CLARIFICATION_REQUIRED.value,
            response_status=ResponseStatus.CLARIFICATION_REQUIRED.value,
            direct_answer=AnswerClaim("clarification", "Which Williams do you mean?", "clarification_required", ["interpretation"], "low"),
            recommendation=None,
            clarification_required=True,
        )
        self.assertTrue(validate_rendered_answer(clarification, "Which Williams do you mean?").used_openai_response)

        unsupported = approved_trade_packet(answer_mode=AnswerMode.UNSUPPORTED.value, recommendation=None)
        self.assertEqual(validate_rendered_answer(unsupported, "You should accept the offer.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

        blocked = approved_trade_packet(answer_mode=AnswerMode.BLOCKED.value, recommendation=None)
        self.assertEqual(validate_rendered_answer(blocked, "Make the trade now.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

        failed = approved_trade_packet(answer_mode=AnswerMode.FAILED.value, recommendation=None, direct_answer=AnswerClaim("failed", "I cannot safely assemble an answer right now.", "failed", ["answer"], "unavailable"))
        self.assertTrue(validate_rendered_answer(failed, "I cannot safely assemble an answer right now.").used_openai_response)

    def test_team_identity_fallback_preserves_direct_fact(self):
        factual = approved_trade_packet(
            answer_mode=AnswerMode.DIRECT_FACT.value,
            response_status=ResponseStatus.READY.value,
            direct_answer=AnswerClaim("factual", "You manage Condor Dynasty in Legacy League.", "ready", ["team_evidence"], "high"),
            recommendation=None,
            facts=[],
            calculations=[],
            reasons=[],
        )

        fallback = render_answer_packet_fallback(factual)
        self.assertIn("Condor Dynasty", fallback)
        self.assertIn("Legacy League", fallback)
        self.assertNotIn("My recommendation", fallback)
        self.assertNotIn("validated recommendation", fallback.lower())
        self.assertNotIn("not applicable", fallback.lower())

        validation = validate_rendered_answer(factual, "")
        self.assertEqual(validation.validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertIn("Condor Dynasty", validation.approved_text)

    def test_roster_fallback_preserves_player_list(self):
        factual = approved_trade_packet(
            answer_mode=AnswerMode.DIRECT_FACT.value,
            response_status=ResponseStatus.READY.value,
            direct_answer=AnswerClaim("factual", "You currently have 2 players on your roster:\n- Josh Allen - QB\n- Taxi Player - RB - TAXI", "ready", ["player_evidence"], "high"),
            recommendation=None,
            facts=[],
            calculations=[],
            reasons=[],
        )

        validation = validate_rendered_answer(factual, "")
        self.assertEqual(validation.validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertIn("Josh Allen", validation.approved_text)
        self.assertIn("Taxi Player", validation.approved_text)
        self.assertNotIn("My recommendation", validation.approved_text)
        self.assertNotIn("validated recommendation", validation.approved_text.lower())

    def test_non_applicable_recommendation_fallback_renders_naturally(self):
        factual = approved_trade_packet(
            answer_mode=AnswerMode.DIRECT_FACT.value,
            response_status=ResponseStatus.READY.value,
            direct_answer=AnswerClaim(
                "no_structured_recommendation",
                "This question does not require a structured transaction recommendation.",
                "ready",
                ["decision"],
                "high",
            ),
            recommendation=None,
            facts=[],
            calculations=[],
            reasons=[],
        )

        fallback = render_answer_packet_fallback(factual)

        self.assertIn("does not require", fallback)
        self.assertNotIn("The validated recommendation is to not applicable", fallback)
        self.assertNotIn("DecisionOutput", fallback)

    def test_recommendation_action_reversals_and_target_replacement_fallback(self):
        answer = approved_trade_packet()

        self.assertTrue(validate_rendered_answer(answer, "My recommendation: accept Accept.").used_openai_response)
        self.assertTrue(validate_rendered_answer(answer, "I would accept Accept here.").used_openai_response)
        self.assertEqual(validate_rendered_answer(answer, "My recommendation: reject this.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertEqual(validate_rendered_answer(answer, "My recommendation: acquire Accept.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertEqual(validate_rendered_answer(answer, "My recommendation: accept Player B instead.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

    def test_rejected_recommendation_protection(self):
        answer = limited_packet()

        validation = validate_rendered_answer(answer, "I cannot validate a supported recommendation from the available data.")
        self.assertTrue(validation.used_openai_response)

        invented = validate_rendered_answer(answer, "You should accept the trade.")
        self.assertEqual(invented.validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertIn("invented_recommendation", invented.unsupported_claims)

    def test_legality_preservation(self):
        illegal = approved_trade_packet(
            recommendation=None,
            rule_conclusions=[replace(packet().rule_conclusions[0], status="violated", explanation="The move is illegal.", blocking=True)],
            direct_answer=AnswerClaim("legal", "The deterministic rules result is illegal.", "ready", ["rules"], "high"),
        )
        self.assertTrue(validate_rendered_answer(illegal, "The move is illegal.").used_openai_response)
        self.assertEqual(validate_rendered_answer(illegal, "The move is legal and allowed.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

        conditional = approved_trade_packet(response_status=ResponseStatus.READY_WITH_CONDITIONS.value)
        self.assertEqual(validate_rendered_answer(conditional, "This is definitely legal and cleared to proceed.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

        unresolved = approved_trade_packet(rule_conclusions=[replace(packet().rule_conclusions[0], status="unresolved", explanation="Rule source unavailable.", blocking=True)])
        self.assertEqual(validate_rendered_answer(unresolved, "This is confirmed legal.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

    def test_actionability_and_execution_claims(self):
        not_actionable = approved_trade_packet(approved_for_action=False, recommendation=replace(packet().recommendation, actionable_now=False))

        self.assertTrue(validate_rendered_answer(not_actionable, "My recommendation is to accept Accept, but this is not ready to act on yet.").used_openai_response)
        self.assertEqual(validate_rendered_answer(not_actionable, "My recommendation is accept Accept. Go ahead and make the trade now.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertEqual(validate_rendered_answer(not_actionable, "I completed the trade for you.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

    def test_conditions_required_optional_and_duplicates(self):
        base = packet(rule_eval=rules(plan(), status="conditionally_legal"))
        condition = AnswerCondition("cond1", "post_trade_cap", "remain under the salary cap", None, True, ["rules"])
        optional = AnswerCondition("cond2", "minor", "optional note", None, False, ["rules"])
        answer = approved_trade_packet(conditions=[condition, condition, optional], approved_for_action=False, response_status=ResponseStatus.READY_WITH_CONDITIONS.value)

        self.assertTrue(validate_rendered_answer(answer, "Accept Accept only if you remain under the salary cap. This is not ready to act on yet.").used_openai_response)
        self.assertEqual(validate_rendered_answer(answer, "Accept Accept.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

    def test_numeric_fidelity_currency_percentage_roster_season_contract_years(self):
        answer = approved_trade_packet()
        self.assertTrue(validate_rendered_answer(answer, "Accept Accept. The value delta is $8.").used_openai_response)
        self.assertTrue(validate_rendered_answer(answer, "Accept Accept. The value delta is 8.").used_openai_response)
        self.assertEqual(validate_rendered_answer(answer, "Accept Accept. The value delta is 21.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

        fact_answer = approved_trade_packet(
            answer_mode=AnswerMode.DIRECT_FACT.value,
            direct_answer=AnswerClaim("fact", "Your verified available cap is $12000000 for 2026.", "ready", ["fact"], "high"),
            recommendation=None,
            facts=[replace(packet().facts[0], label="Available cap", value=12000000, unit="cap dollars")],
        )
        self.assertTrue(validate_rendered_answer(fact_answer, "Your verified available cap is $12 million for 2026.").used_openai_response)
        self.assertEqual(validate_rendered_answer(fact_answer, "Your verified available cap is $21 million for 2026.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

    def test_estimate_label_validation(self):
        estimated = replace(packet().calculations[0], estimated=True, exact=False)
        answer = approved_trade_packet(calculations=[estimated], confidence="medium")

        self.assertTrue(validate_rendered_answer(answer, "Accept Accept. The value delta is approximately 8.").used_openai_response)
        self.assertEqual(validate_rendered_answer(answer, "Accept Accept. The exact value delta is 8.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

    def test_entity_validation_supported_unsupported_generic_alias(self):
        answer = approved_trade_packet()

        self.assertTrue(validate_rendered_answer(answer, "Accept Accept. This helps your roster.").used_openai_response)
        self.assertEqual(validate_rendered_answer(answer, "Accept Justin Jefferson instead.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertTrue(validate_rendered_answer(answer, "Accept Accept because the player value supports it.").used_openai_response)

    def test_forbidden_claim_categories(self):
        answer = approved_trade_packet()
        cases = [
            "Accept Accept because the other owner will accept.",
            "Accept Accept. I completed the trade.",
            "Accept Accept based on fabricated projections.",
            "Accept Accept. The future pick is locked in at the exact slot.",
            "Accept Accept. He is definitely available to add.",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(validate_rendered_answer(answer, text).validation_status, RenderedValidationStatus.FALLBACK_USED.value)

    def test_limitations_material_optional_and_confidence_language(self):
        limitation = AnswerLimitation("missing_projection", "trusted projections are unavailable", "Answer is limited by missing projection support.", ["projection"])
        answer = approved_trade_packet(limitations=[limitation], confidence="low")

        self.assertTrue(validate_rendered_answer(answer, "Accept Accept, but trusted projections are unavailable.").used_openai_response)
        self.assertEqual(validate_rendered_answer(answer, "Accept Accept. This is guaranteed.").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

        optional = approved_trade_packet(limitations=[replace(limitation, effect_on_answer="Minor note only.")])
        validation = validate_rendered_answer(optional, "Accept Accept.")
        self.assertTrue(validation.used_openai_response)

    def test_clarification_exact_semantic_multiple_and_speculative(self):
        answer = approved_trade_packet(
            answer_mode=AnswerMode.CLARIFICATION_REQUIRED.value,
            response_status=ResponseStatus.CLARIFICATION_REQUIRED.value,
            direct_answer=AnswerClaim("clarification", "Which Williams do you mean?", "clarification_required", ["interpretation"], "low"),
            recommendation=None,
        )

        self.assertTrue(validate_rendered_answer(answer, "Which Williams do you mean?").used_openai_response)
        self.assertTrue(validate_rendered_answer(answer, "Which Williams are you referring to?").used_openai_response)
        self.assertEqual(validate_rendered_answer(answer, "Which Williams do you mean? Which trade side?").validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertEqual(validate_rendered_answer(answer, "You should accept. Which Williams do you mean?").validation_status, RenderedValidationStatus.FALLBACK_USED.value)

    def test_internal_terms_and_formatting(self):
        answer = approved_trade_packet()
        self.assertTrue(validate_rendered_answer(answer, "**Accept Accept**\n- Value supports it.").used_openai_response)
        bad = ["AnswerPacket says accept.", "DecisionOutput supports this.", "Stage 12 approved this.", '{"answer_mode": "recommendation"}', "<script>alert(1)</script>", "/Users/tommy/secret", "OPENAI_API_KEY=abc"]
        for text in bad:
            with self.subTest(text=text):
                self.assertEqual(validate_rendered_answer(answer, text).validation_status, RenderedValidationStatus.FALLBACK_USED.value)
        self.assertTrue(validate_rendered_answer(answer, "Using the available league data, accept Accept.").used_openai_response)

    def test_fallback_renderer_modes_bounds_and_no_internal_names(self):
        modes = [
            AnswerMode.DIRECT_FACT.value,
            AnswerMode.DIRECT_RULES.value,
            AnswerMode.RECOMMENDATION.value,
            AnswerMode.CONDITIONAL_RECOMMENDATION.value,
            AnswerMode.COMPARISON.value,
            AnswerMode.RANKED_OPTIONS.value,
            AnswerMode.LIMITED_INFORMATION.value,
            AnswerMode.CLARIFICATION_REQUIRED.value,
            AnswerMode.UNSUPPORTED.value,
            AnswerMode.BLOCKED.value,
            AnswerMode.FAILED.value,
        ]
        for mode in modes:
            with self.subTest(mode=mode):
                answer = approved_trade_packet(answer_mode=mode)
                if mode in {AnswerMode.LIMITED_INFORMATION.value, AnswerMode.CLARIFICATION_REQUIRED.value, AnswerMode.UNSUPPORTED.value, AnswerMode.BLOCKED.value, AnswerMode.FAILED.value}:
                    answer = approved_trade_packet(answer_mode=mode, recommendation=None)
                text = render_answer_packet_fallback(answer)
                self.assertTrue(text)
                self.assertLessEqual(len(text), 3500)
                self.assertNotIn("AnswerPacket", text)
                self.assertNotIn("source_refs", text)

    def test_serialization_excludes_raw_packets_secrets_and_chain_of_thought(self):
        validation = validate_rendered_answer(approved_trade_packet(), "Accept Accept.")
        payload = build_rendered_answer_validation_payload(validation)
        text = repr(payload).lower()

        self.assertIn("validation_status", payload)
        self.assertIn("approved_text", payload)
        self.assertIn("used_deterministic_fallback", payload)
        self.assertNotIn("service_key", text)
        self.assertNotIn("chain_of_thought", text)
        self.assertNotIn("raw_row", text)

    def test_openai_integration_compliant_noncompliant_absent_no_packet_and_one_call(self):
        service = importlib.import_module("gm_assistant.openai_service")
        sb = FakeSupabase()
        identity = service.AssistantIdentity("Tommy Bruggeman", "user-1", "league-1", "team-1")
        answer_packet = approved_trade_packet()

        compliant_client = FakeOpenAI([final_response("My recommendation: accept Accept.")])
        compliant = service.answer_gm_question("Should I accept?", identity, sb=sb, client=compliant_client, answer_packet=answer_packet)
        self.assertEqual(compliant.text, "My recommendation: accept Accept.")
        self.assertEqual(len(compliant_client.responses.calls), 1)
        self.assertTrue(compliant.rendered_validation.used_openai_response)

        bad_client = FakeOpenAI([final_response("My recommendation: reject this.")])
        bad = service.answer_gm_question("Should I accept?", identity, sb=sb, client=bad_client, answer_packet=answer_packet)
        self.assertNotEqual(bad.text, "My recommendation: reject this.")
        self.assertTrue(bad.rendered_validation.used_deterministic_fallback)
        self.assertEqual(len(bad_client.responses.calls), 1)

        empty_client = FakeOpenAI([final_response("")])
        empty = service.answer_gm_question("Should I accept?", identity, sb=sb, client=empty_client, answer_packet=answer_packet)
        self.assertTrue(empty.rendered_validation.used_deterministic_fallback)

        no_packet_client = FakeOpenAI([final_response("Any old compatible path.")])
        no_packet = service.answer_gm_question("Who am I?", identity, sb=sb, client=no_packet_client)
        self.assertEqual(no_packet.text, "Any old compatible path.")
        self.assertIsNone(no_packet.rendered_validation)
        self.assertEqual(no_packet_client.responses.calls[0]["input"][-1]["content"], "Who am I?")

    def test_approved_reasoning_response_reaches_final_service_answer(self):
        service = importlib.import_module("gm_assistant.openai_service")
        sb = FakeSupabase()
        identity = service.AssistantIdentity("Tommy Bruggeman", "user-1", "league-1", "team-1")
        answer_packet = approved_trade_packet(
            answer_mode=AnswerMode.DIRECT_FACT.value,
            response_status=ResponseStatus.READY.value,
            direct_answer=AnswerClaim(
                "football_structure_limited",
                "I could not verify enough scoped roster-construction data to answer that football-structure question.",
                "ready",
                ["answer"],
                "low",
            ),
            recommendation=None,
            facts=[],
            calculations=[],
            reasons=[],
        )
        direct_answer = "Your best player from the approved OpenAI response is Garrett Wilson."
        provider = FakeReasoningProvider(ReasoningResponse(
            answer_type="factual_explanation",
            direct_answer=direct_answer,
            recommendation=None,
            recommendation_strength="none",
            key_reasons=[],
            main_risks=[],
            alternatives=[],
            clarifying_question=None,
            facts_used=["answer.direct_answer"],
            limitations=[],
            constraint_conflicts=[],
            requires_deterministic_follow_up=False,
        ))

        answer = service.answer_gm_question(
            "Who is my best player?",
            identity,
            sb=sb,
            answer_packet=answer_packet,
            reasoning_provider=provider,
        )

        self.assertEqual(answer.raw_rendered_text, direct_answer)
        self.assertEqual(answer.text, direct_answer)
        self.assertTrue(answer.rendered_validation.used_openai_response)
        self.assertFalse(answer.rendered_validation.used_deterministic_fallback)
        self.assertEqual(answer.reasoning_trace.final_answer_source, "openai")
        self.assertEqual(answer.reasoning_trace.fallback_status, "not_used")
        self.assertIsNone(answer.reasoning_trace.fallback_reason)


if __name__ == "__main__":
    unittest.main()
