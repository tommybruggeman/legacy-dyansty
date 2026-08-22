from __future__ import annotations

import importlib
import sys
import types
import unittest
from dataclasses import replace


auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

from gm_assistant.answer_packet import (
    AnswerMode,
    ResponseStatus,
    build_answer_packet,
    build_answer_packet_payload,
)
from gm_assistant.calculations import CalculationPacket, CalculationResult, CalculationStatus, UnresolvedCalculation
from gm_assistant.decision import DecisionAction, DecisionOutput, DecisionType, RecommendationStatus
from gm_assistant.evidence import CapEvidence, ContractEvidence, DraftPickEvidence, FreeAgentEvidence, LineupEvidence, TeamEvidence
from gm_assistant.interpretation import Ambiguity, Intent, InterpretedQuestion
from gm_assistant.objective import Goal
from gm_assistant.planning import PlanBlocker, PlanType
from gm_assistant.rules import RuleCondition, RuleViolation, RulesEvaluation, UnresolvedRule
from gm_assistant.validation import RecommendationValidation, ValidationIssue, ValidationStatus
from tests.test_assistant_validation import (
    calc_packet,
    calc_request,
    context,
    decision,
    evidence,
    hard_constraint,
    interpreted,
    objective,
    plan,
    player,
    result,
    rules,
    state,
    team,
    validate,
)


def packet(for_plan=None, **overrides):
    p = for_plan or plan(calculations=[calc_request("trade_value_delta")])
    ctx = overrides.get("ctx") or context()
    conv = overrides.get("conv")
    if "conv" not in overrides:
        conv = state(ctx)
    interp = overrides.get("interp") or interpreted()
    obj = overrides.get("obj") or objective()
    ev = overrides.get("ev") or evidence(p, ctx)
    rule_eval = overrides.get("rule_eval") or rules(p)
    calcs = overrides.get("calcs") or calc_packet(p, results=[result()])
    out = overrides.get("out") or decision(p, ctx=ctx, conv=conv, interp=interp, obj=obj, ev=ev, rule_eval=rule_eval, calcs=calcs)
    validation = overrides.get("validation")
    if validation is None:
        validation = validate(p, ctx=ctx, conv=conv, interp=interp, obj=obj, ev=ev, rule_eval=rule_eval, calcs=calcs, out=out)
    return build_answer_packet(
        context=ctx,
        conversation_state=conv,
        interpreted_question=interp,
        owner_objective=obj,
        decision_plan=p,
        evidence_packet=ev,
        rules_evaluation=rule_eval,
        calculation_packet=calcs,
        decision_output=out,
        recommendation_validation=validation,
    )


class AnswerPacketStage11Test(unittest.TestCase):
    def test_matching_stage_1_to_10_scope_succeeds(self):
        answer = packet()

        self.assertEqual(answer.response_status, ResponseStatus.READY.value)
        self.assertIsNotNone(answer.recommendation)
        self.assertTrue(answer.approved_for_explanation)

    def test_mismatched_user_league_team_and_conversation_block(self):
        p = plan()
        cases = [
            {"conv": state(user_id="user-x")},
            {"conv": state(league_id="league-x")},
            {"conv": state(league_team_id="team-x")},
            {"conv": state(conversation_id="conversation-x")},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                answer = packet(p, **kwargs)
                self.assertEqual(answer.response_status, ResponseStatus.BLOCKED.value)
                self.assertIsNone(answer.recommendation)

    def test_mismatched_plan_evidence_rules_calculations_and_validation_block(self):
        p = plan()
        self.assertEqual(packet(p, ev=replace(evidence(p), plan_type="other")).response_status, ResponseStatus.BLOCKED.value)
        self.assertEqual(packet(p, rule_eval=RulesEvaluation("other", "legal")).response_status, ResponseStatus.BLOCKED.value)
        self.assertEqual(packet(p, calcs=CalculationPacket("other", p.decision_engine)).response_status, ResponseStatus.BLOCKED.value)
        bad_validation = RecommendationValidation(
            ValidationStatus.APPROVED.value,
            True,
            True,
            decision_action="reject",
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            confidence_after_validation="high",
            validation_complete=True,
        )
        self.assertEqual(packet(p, validation=bad_validation).response_status, ResponseStatus.BLOCKED.value)

    def test_cross_league_or_missing_decision_reference_blocks(self):
        p = plan()
        out = DecisionOutput(
            DecisionType.TRADE_EVALUATION.value,
            DecisionAction.ACCEPT.value,
            RecommendationStatus.RECOMMENDED.value,
            evidence_refs=["ghost-player"],
            actionable_now=True,
            recommendation_complete=True,
            confidence="high",
        )
        validation = RecommendationValidation(ValidationStatus.APPROVED.value, True, True, decision_action=out.action, recommendation_status=out.recommendation_status, confidence_after_validation="high", validation_complete=True)

        answer = packet(p, out=out, validation=validation)

        self.assertEqual(answer.response_status, ResponseStatus.BLOCKED.value)

    def test_answer_modes_cover_fact_rules_comparison_ranked_conditional_general_unsupported_failed(self):
        factual = plan(PlanType.FACTUAL_LOOKUP.value, "factual_lookup_engine")
        self.assertEqual(packet(factual, interp=interpreted(Intent.DATA_LOOKUP.value), obj=objective(Goal.FACTUAL_LOOKUP.value), calcs=calc_packet(factual)).answer_mode, AnswerMode.DIRECT_FACT.value)

        rule_plan = plan(PlanType.RULES_LOOKUP.value, "rules_explanation_engine")
        self.assertEqual(packet(rule_plan, interp=interpreted(Intent.RULES_QUESTION.value), obj=objective(Goal.UNDERSTAND_RULES.value), calcs=calc_packet(rule_plan)).answer_mode, AnswerMode.DIRECT_RULES.value)

        comparison = plan(PlanType.PLAYER_COMPARISON.value, "player_comparison_engine")
        ev = evidence(comparison, players=[player("p1", value=80), player("p2", value=60)])
        self.assertEqual(packet(comparison, interp=interpreted(Intent.PLAYER_COMPARISON.value), obj=objective(Goal.COMPARE_ASSETS.value), ev=ev, calcs=calc_packet(comparison)).answer_mode, AnswerMode.COMPARISON.value)

        discovery = plan(PlanType.TRADE_DISCOVERY.value, "trade_discovery_engine")
        discovery_ev = evidence(discovery, players=[player("p2", team_id="team-2")])
        discovery_out = decision(discovery, interp=interpreted(Intent.TRADE_DISCOVERY.value), ev=discovery_ev, calcs=calc_packet(discovery))
        discovery_validation = RecommendationValidation(
            ValidationStatus.APPROVED.value,
            True,
            False,
            decision_action=discovery_out.action,
            recommendation_status=discovery_out.recommendation_status,
            confidence_after_validation="medium",
            validation_complete=True,
        )
        self.assertEqual(packet(discovery, interp=interpreted(Intent.TRADE_DISCOVERY.value), ev=discovery_ev, calcs=calc_packet(discovery), out=discovery_out, validation=discovery_validation).answer_mode, AnswerMode.RANKED_OPTIONS.value)

        condition = RuleCondition("post_trade_cap", "Cap must be verified.", None, ["cap"], [], True)
        conditional_rules = rules(plan(), status="conditionally_legal", conditions=[condition])
        conditional_out = decision(plan(), rule_eval=conditional_rules)
        conditional_validation = RecommendationValidation(
            ValidationStatus.APPROVED_WITH_CONDITIONS.value,
            True,
            False,
            decision_action=conditional_out.action,
            recommendation_status=conditional_out.recommendation_status,
            confidence_after_validation="medium",
            validation_complete=True,
        )
        self.assertEqual(packet(rule_eval=conditional_rules, out=conditional_out, validation=conditional_validation).answer_mode, AnswerMode.CONDITIONAL_RECOMMENDATION.value)

        general = plan(PlanType.GENERAL_CONVERSATION.value, "conversation_engine")
        self.assertEqual(packet(general, interp=interpreted(Intent.GENERAL_CONVERSATION.value), obj=objective(Goal.UNCLEAR.value), calcs=calc_packet(general)).answer_mode, AnswerMode.GENERAL_CONVERSATION.value)

        unsupported = plan(PlanType.UNSUPPORTED.value, "unsupported_engine")
        self.assertEqual(packet(unsupported, interp=interpreted(Intent.UNSUPPORTED.value), calcs=calc_packet(unsupported)).answer_mode, AnswerMode.UNSUPPORTED.value)

        failed_validation = RecommendationValidation(ValidationStatus.FAILED.value, False, False, decision_action="accept", recommendation_status="recommended")
        self.assertEqual(packet(validation=failed_validation).answer_mode, AnswerMode.FAILED.value)

    def test_validation_gate_omits_rejected_and_does_not_replace(self):
        p = plan()
        validation = RecommendationValidation(
            ValidationStatus.REJECTED.value,
            False,
            False,
            errors=[ValidationIssue("test_rejection", "blocking", "Rejected by validation.", ["validation"], True)],
            decision_action="accept",
            recommendation_status="recommended",
            confidence_after_validation="unavailable",
        )

        answer = packet(p, validation=validation)

        self.assertEqual(answer.answer_mode, AnswerMode.LIMITED_INFORMATION.value)
        self.assertIsNone(answer.recommendation)
        self.assertTrue(any(item.claim_type == "replacement_recommendation" for item in answer.forbidden_claims))

    def test_action_approval_false_preserves_recommendation_but_disables_actionability(self):
        p = plan()
        validation = validate(p)
        validation = replace(validation, approved_for_action=False)

        answer = packet(p, validation=validation)

        self.assertIsNotNone(answer.recommendation)
        self.assertFalse(answer.recommendation.actionable_now)
        self.assertTrue(any(item.claim_type == "actionability" for item in answer.forbidden_claims))

    def test_factual_assembly_cap_contract_pick_roster_season_units_and_no_strategy(self):
        p = plan(PlanType.FACTUAL_LOOKUP.value, "factual_lookup_engine")
        ev = replace(evidence(
            p,
            contracts=[ContractEvidence("p1", "team-1", 2026, 12, 2, "Active", {}, {}, ["contracts"])],
            picks=[DraftPickEvidence("2027_1", 2027, 1, None, "team-1", "team-1", "available", True, ["draft_picks"])],
            players=[player("p1")],
        ), cap_evidence=[CapEvidence("team-1", 2026, 250, 220, 5, 25, {}, {}, ["cap"])])

        answer = packet(p, interp=interpreted(Intent.DATA_LOOKUP.value), obj=objective(Goal.FACTUAL_LOOKUP.value), ev=ev, calcs=calc_packet(p))

        fact_types = {fact.fact_type for fact in answer.facts}
        self.assertIn("cap_space", fact_types)
        self.assertIn("contract", fact_types)
        self.assertIn("draft_pick_ownership", fact_types)
        self.assertIn("player_profile", fact_types)
        self.assertTrue(any(fact.unit == "cap dollars" for fact in answer.facts))
        self.assertIsNone(answer.recommendation)

    def test_team_identity_direct_answer_is_user_facing(self):
        p = plan(PlanType.FACTUAL_LOOKUP.value, "factual_lookup_engine")
        team_ev = TeamEvidence("team-1", "Condor Dynasty", "Owner One", [], {"league_name": "Legacy League"}, {}, {}, ["league_teams"])
        ev = evidence(p, teams=[team_ev], players=[])
        interp = InterpretedQuestion("what team do I manage", Intent.DATA_LOOKUP.value, confidence="high")

        answer = packet(p, interp=interp, obj=objective(Goal.FACTUAL_LOOKUP.value), ev=ev, calcs=calc_packet(p))

        self.assertEqual(answer.answer_mode, AnswerMode.DIRECT_FACT.value)
        self.assertIsNone(answer.recommendation)
        self.assertIsNotNone(answer.direct_answer)
        self.assertEqual(answer.direct_answer.text, "You manage Condor Dynasty in Legacy League.")
        self.assertNotIn("validated recommendation", answer.direct_answer.text.lower())
        self.assertNotIn("not applicable", answer.direct_answer.text.lower())

    def test_rules_assembly_preserves_legal_illegal_conditional_and_unverifiable(self):
        p = plan(PlanType.RULES_LOOKUP.value, "rules_explanation_engine")
        for status in ("legal", "illegal", "conditionally_legal", "unverifiable"):
            with self.subTest(status=status):
                condition = [RuleCondition("deadline", "Before deadline.", None, [], [], True)] if status == "conditionally_legal" else []
                violation = [RuleViolation("deadline", "trade_deadline", "Deadline passed.", [], True)] if status == "illegal" else []
                rule_eval = rules(p, status=status, conditions=condition, violations=violation)
                answer = packet(p, interp=interpreted(Intent.RULES_QUESTION.value), obj=objective(Goal.UNDERSTAND_RULES.value), rule_eval=rule_eval, calcs=calc_packet(p))
                self.assertTrue(any(item.status == status or item.status in {"violated", "conditional"} for item in answer.rule_conclusions))

    def test_limited_information_keeps_verified_facts_for_system_missing_data(self):
        p = plan()
        calcs = calc_packet(p, complete=False)
        validation = RecommendationValidation(ValidationStatus.BLOCKED.value, False, False, decision_action="accept", recommendation_status="recommended", confidence_after_validation="unavailable")

        answer = packet(p, calcs=calcs, validation=validation)

        self.assertEqual(answer.response_status, ResponseStatus.BLOCKED.value)
        self.assertTrue(answer.facts)
        self.assertIsNone(answer.recommendation)

    def test_clarification_prompt_is_deterministic_and_single_question(self):
        p = plan()
        interp = replace(interpreted(), ambiguities=[Ambiguity("player_identity", "Williams", ["w1", "w2"], True, "Multiple Williams matches.")])

        answer = packet(p, interp=interp)

        self.assertEqual(answer.response_status, ResponseStatus.CLARIFICATION_REQUIRED.value)
        self.assertEqual(answer.direct_answer.text.count("?"), 1)

    def test_non_user_resolvable_data_gap_does_not_ask_clarification(self):
        p = plan()
        rule_eval = replace(rules(p), unresolved_rules=[UnresolvedRule("injury_source", "Missing injury feed.", True, ["injuries"], [])], overall_status="unverifiable", rules_complete=False)

        answer = packet(p, rule_eval=rule_eval)

        self.assertNotEqual(answer.response_status, ResponseStatus.CLARIFICATION_REQUIRED.value)
        self.assertTrue(answer.limitations)

    def test_calculation_exact_estimated_assumptions_unit_method_and_bounds(self):
        p = plan()
        many = [
            CalculationResult(f"calc_{i}", CalculationStatus.ESTIMATED.value if i == 0 else CalculationStatus.SUCCESS.value, i, "points", f"calc_{i}", "method", "formula", assumptions=["assumption"], exact=i != 0, estimated=i == 0)
            for i in range(8)
        ]
        out = DecisionOutput(DecisionType.TRADE_EVALUATION.value, DecisionAction.ACCEPT.value, RecommendationStatus.RECOMMENDED.value, supporting_calculations=[], actionable_now=False, recommendation_complete=True)
        validation = RecommendationValidation(ValidationStatus.APPROVED.value, True, False, decision_action=out.action, recommendation_status=out.recommendation_status, confidence_after_validation="medium", validation_complete=True)

        answer = packet(p, calcs=calc_packet(p, results=many), out=out, validation=validation)

        self.assertLessEqual(len(answer.calculations), 5)
        self.assertTrue(any(calc.estimated for calc in answer.calculations))
        self.assertTrue(any(calc.unit == "points" for calc in answer.calculations))
        self.assertTrue(any(calc.method == "method" for calc in answer.calculations))

    def test_conditions_warnings_limitations_and_forbidden_claims_are_preserved(self):
        p = plan()
        condition = RuleCondition("post_trade_cap", "Cap must be verified.", None, ["cap"], [], True)

        answer = packet(p, rule_eval=rules(p, status="conditionally_legal", conditions=[condition]), calcs=calc_packet(p, results=[result(estimated=True)]))

        self.assertTrue(answer.conditions)
        self.assertFalse(answer.approved_for_action)
        self.assertTrue(any(item.warning_type == "estimated_calculation" for item in answer.warnings))
        self.assertTrue(any(item.claim_type == "confirmed_legality" for item in answer.forbidden_claims))
        self.assertTrue(any(item.claim_type == "exact_estimate" for item in answer.forbidden_claims))

    def test_bounds_and_source_references_are_enforced_and_deduplicated(self):
        p = plan()
        ev = evidence(p, players=[player(f"p{i}") for i in range(20)], teams=[team("team-1")])

        answer = packet(p, ev=ev)
        payload = build_answer_packet_payload(answer)

        self.assertLessEqual(len(answer.facts), 8)
        self.assertLessEqual(len(answer.source_index), 20)
        self.assertEqual(len({ref.ref_id for ref in answer.source_index}), len(answer.source_index))
        self.assertIn("source_index", payload)

    def test_audit_trace_is_bounded_and_has_no_hidden_reasoning(self):
        answer = packet()
        text = repr(build_answer_packet_payload(answer)).lower()

        self.assertLessEqual(len(answer.audit_events), 20)
        for event in ("interpretation", "objective", "planning", "evidence", "rules", "calculations", "decision", "validation", "answer_assembled"):
            self.assertIn(event, text)
        self.assertNotIn("chain_of_thought", text)
        self.assertNotIn("scratchpad", text)

    def test_serialization_excludes_raw_upstream_packets_exceptions_sql_and_secrets(self):
        answer = packet()
        payload = build_answer_packet_payload(answer)
        text = repr(payload).lower()

        self.assertIn("direct_answer", payload)
        self.assertIn("forbidden_claims", payload)
        self.assertNotIn("raw_row", text)
        self.assertNotIn("exception", text)
        self.assertNotIn("sql", text)
        self.assertNotIn("service_key", text)

    def test_openai_service_accepts_answer_packet_as_highest_priority_and_final_question(self):
        service = importlib.import_module("gm_assistant.openai_service")
        answer = packet()
        p = plan()

        messages = service._build_initial_messages(
            "Should I accept?",
            [],
            state(),
            interpreted(),
            objective(),
            p,
            evidence(p),
            rules(p),
            calc_packet(p, results=[result()]),
            decision(p),
            validate(p),
            answer,
        )

        self.assertEqual(messages[-1]["content"], "Should I accept?")
        self.assertTrue(any("Structured deterministic answer contract" in item["content"] for item in messages))
        self.assertTrue(any("forbidden_claims" in item["content"] for item in messages))


if __name__ == "__main__":
    unittest.main()
