from __future__ import annotations

import importlib
import sys
import types
import unittest
from dataclasses import replace


auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

from gm_assistant.calculations import CalculationPacket, CalculationResult, CalculationStatus, UnresolvedCalculation
from gm_assistant.conversation_state import ConversationState
from gm_assistant.decision import (
    DecisionAction,
    DecisionOutput,
    DecisionType,
    RecommendationStatus,
    build_decision_output,
)
from gm_assistant.evidence import (
    ContractEvidence,
    DraftPickEvidence,
    EvidenceContextRef,
    EvidencePacket,
    FreeAgentEvidence,
    LineupEvidence,
    PlayerEvidence,
    TeamEvidence,
)
from gm_assistant.interpretation import Intent, InterpretedQuestion
from gm_assistant.objective import Goal, ObjectiveConstraint, OwnerObjective
from gm_assistant.planning import CalculationRequest, DecisionPlan, PlanType
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE
from gm_assistant.rules import RuleCondition, RuleViolation, RulesEvaluation
from gm_assistant.validation import (
    ValidationStatus,
    build_validation_packet,
    validate_recommendation,
)


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
        "timezone": "America/Boise",
        "team_name": "Team One",
        "owner_name": "Owner One",
    }
    data.update(overrides)
    return AssistantRequestContext(**data)


def state(ctx=None, **overrides):
    ctx = ctx or context()
    data = {
        "conversation_id": ctx.conversation_id,
        "user_id": ctx.user_id,
        "league_id": ctx.league_id,
        "league_team_id": ctx.league_team_id,
    }
    data.update(overrides)
    return ConversationState(**data)


def interpreted(intent=Intent.TRADE_EVALUATION.value):
    return InterpretedQuestion("Should I do this?", intent, confidence="high")


def objective(goal=Goal.CONSTRUCT_TRANSACTION.value, *, constraints=None):
    return OwnerObjective(
        request_goal=goal,
        active_strategic_goal=goal,
        primary_goal=goal,
        non_negotiables=list(constraints or []),
        confidence="high",
    )


def hard_constraint(kind, value=True):
    return ObjectiveConstraint(kind, value, "explicit_current_message", True, "current_request")


def plan(plan_type=PlanType.TRADE_EVALUATION.value, engine="trade_evaluation_engine", *, calculations=None):
    return DecisionPlan(
        plan_type=plan_type,
        request_goal=Goal.CONSTRUCT_TRANSACTION.value,
        active_strategic_goal=Goal.CONSTRUCT_TRANSACTION.value,
        decision_engine=engine,
        response_mode="structured_evaluation",
        calculation_requests=list(calculations or []),
        ready_for_execution=True,
        confidence="high",
    )


def calc_request(kind):
    return CalculationRequest(kind, True, f"Calculate {kind}.", [], kind)


def player(pid="p1", *, team_id="team-1", free=False, value=70):
    return PlayerEvidence(
        pid,
        f"Player {pid}",
        "WR",
        "NYJ",
        24,
        2,
        "Active",
        None if free else team_id,
        free,
        {"asset_score": value},
        {"overall_value_score": value},
        ["player_strategic_profiles"],
    )


def team(team_id="team-1"):
    return TeamEvidence(team_id, "Team One", "Owner One", ["p1"], {}, {"team_direction": "CONTEND_NOW"}, {}, ["team_brain"])


def evidence(for_plan, ctx=None, **parts):
    ctx = ctx or context()
    return EvidencePacket(
        request_context_ref=EvidenceContextRef(ctx.user_id, ctx.league_id, ctx.league_team_id, ctx.conversation_id, ctx.current_season, [ctx.requested_season]),
        plan_type=for_plan.plan_type,
        decision_engine=for_plan.decision_engine,
        team_evidence=list(parts.get("teams") or [team(ctx.league_team_id)]),
        player_evidence=list(parts.get("players") or [player("p1", team_id=ctx.league_team_id)]),
        contract_evidence=list(parts.get("contracts") or []),
        draft_pick_evidence=list(parts.get("picks") or []),
        lineup_evidence=list(parts.get("lineups") or []),
        free_agent_evidence=list(parts.get("free_agents") or []),
        required_evidence_complete=parts.get("required_complete", True),
        reduced_mode=parts.get("reduced", False),
        execution_status=parts.get("execution_status", "complete"),
    )


def rules(for_plan, *, status="legal", conditions=None, violations=None):
    return RulesEvaluation(
        for_plan.plan_type,
        overall_status=status,
        violations=list(violations or []),
        conditions=list(conditions or []),
        legal_now=True if status == "legal" else False if status == "illegal" else None,
        conditionally_legal=status == "conditionally_legal",
        blocking_violation=status == "illegal",
        rules_complete=status not in {"blocked", "failed", "unverifiable"},
        reduced_mode=status in {"conditionally_legal", "unverifiable"},
        confidence="low" if status == "unverifiable" else "high",
    )


def calc_packet(for_plan, *, results=None, complete=True, reduced=False):
    return CalculationPacket(
        for_plan.plan_type,
        for_plan.decision_engine,
        results=list(results or []),
        unresolved_calculations=[] if complete else [UnresolvedCalculation("trade_value_delta", "missing", True, ["inputs"])],
        required_calculations_complete=complete,
        reduced_mode=reduced,
        confidence="high" if complete and not reduced else "medium",
    )


def result(kind="trade_value_delta", value=None, *, estimated=False):
    return CalculationResult(
        calculation_type=kind,
        status=CalculationStatus.ESTIMATED.value if estimated else CalculationStatus.SUCCESS.value,
        value=value if value is not None else {"value_delta": 8},
        unit=None,
        output_key=kind,
        method="test_method",
        formula_version="test_formula",
        exact=not estimated,
        estimated=estimated,
        required=True,
        confidence="medium" if estimated else "high",
    )


def decision(for_plan, *, ctx=None, conv=None, interp=None, obj=None, ev=None, rule_eval=None, calcs=None):
    ctx = ctx or context()
    conv = conv if conv is not None else state(ctx)
    interp = interp or interpreted()
    obj = obj or objective()
    ev = ev or evidence(for_plan, ctx)
    rule_eval = rule_eval or rules(for_plan)
    calcs = calcs or calc_packet(for_plan, results=[result()])
    return build_decision_output(
        context=ctx,
        conversation_state=conv,
        interpreted_question=interp,
        owner_objective=obj,
        decision_plan=for_plan,
        evidence_packet=ev,
        rules_evaluation=rule_eval,
        calculation_packet=calcs,
    )


def validate(for_plan, *, ctx=None, conv=None, interp=None, obj=None, ev=None, rule_eval=None, calcs=None, out=None):
    ctx = ctx or context()
    conv = conv if conv is not None else state(ctx)
    interp = interp or interpreted()
    obj = obj or objective()
    ev = ev or evidence(for_plan, ctx)
    rule_eval = rule_eval or rules(for_plan)
    calcs = calcs or calc_packet(for_plan, results=[result()])
    out = out or decision(for_plan, ctx=ctx, conv=conv, interp=interp, obj=obj, ev=ev, rule_eval=rule_eval, calcs=calcs)
    return validate_recommendation(
        context=ctx,
        conversation_state=conv,
        interpreted_question=interp,
        owner_objective=obj,
        decision_plan=for_plan,
        evidence_packet=ev,
        rules_evaluation=rule_eval,
        calculation_packet=calcs,
        decision_output=out,
    )


class RecommendationValidationTest(unittest.TestCase):
    def test_approved_trade_recommendation_is_explainable_and_actionable(self):
        p = plan(calculations=[calc_request("trade_value_delta")])

        validation = validate(p)

        self.assertEqual(validation.validation_status, ValidationStatus.APPROVED.value)
        self.assertTrue(validation.approved_for_explanation)
        self.assertTrue(validation.approved_for_action)
        self.assertEqual(validation.decision_action, DecisionAction.ACCEPT.value)

    def test_scope_mismatch_blocks_validation(self):
        p = plan()

        validation = validate(p, conv=state(league_id="league-x"))

        self.assertEqual(validation.validation_status, ValidationStatus.BLOCKED.value)
        self.assertFalse(validation.approved_for_explanation)
        self.assertFalse(validation.approved_for_action)

    def test_invalid_engine_action_rejects(self):
        p = plan()
        out = DecisionOutput(
            decision_type=DecisionType.LINEUP_DECISION.value,
            action=DecisionAction.ACCEPT.value,
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            actionable_now=True,
            recommendation_complete=True,
            confidence="high",
        )

        validation = validate(p, out=out)

        self.assertEqual(validation.validation_status, ValidationStatus.REJECTED.value)
        self.assertFalse(validation.approved_for_explanation)

    def test_illegal_execution_recommendation_rejects(self):
        p = plan()
        out = DecisionOutput(
            decision_type=DecisionType.TRADE_EVALUATION.value,
            action=DecisionAction.ACCEPT.value,
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            actionable_now=True,
            recommendation_complete=True,
            confidence="high",
        )

        validation = validate(p, rule_eval=rules(p, status="illegal"), out=out)

        self.assertEqual(validation.validation_status, ValidationStatus.REJECTED.value)
        self.assertTrue(validation.contradictions)

    def test_illegal_rejection_is_validated(self):
        p = plan()
        violation = RuleViolation("asset_not_owned", "asset_ownership", "Not owned.", ["p1"], True)
        out = decision(p, rule_eval=rules(p, status="illegal", violations=[violation]))

        validation = validate(p, rule_eval=rules(p, status="illegal", violations=[violation]), out=out)

        self.assertEqual(validation.validation_status, ValidationStatus.APPROVED.value)
        self.assertTrue(validation.approved_for_explanation)
        self.assertFalse(validation.approved_for_action)

    def test_conditional_rules_preserve_conditions_and_disable_action(self):
        p = plan()
        condition = RuleCondition("post_trade_cap_compliance", "Cap must be verified.", None, ["cap_evidence"], [], True)

        validation = validate(p, rule_eval=rules(p, status="conditionally_legal", conditions=[condition]))

        self.assertEqual(validation.validation_status, ValidationStatus.APPROVED_WITH_CONDITIONS.value)
        self.assertFalse(validation.approved_for_action)
        self.assertEqual(validation.conditions[0].condition_type, "post_trade_cap_compliance")

    def test_missing_evidence_references_block(self):
        p = plan()
        out = DecisionOutput(
            decision_type=DecisionType.TRADE_EVALUATION.value,
            action=DecisionAction.ACCEPT.value,
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            evidence_refs=["ghost-player"],
            actionable_now=True,
            recommendation_complete=True,
            confidence="high",
        )

        validation = validate(p, out=out)

        self.assertEqual(validation.validation_status, ValidationStatus.BLOCKED.value)
        self.assertTrue(validation.missing_support)

    def test_missing_required_calculation_blocks_execution(self):
        p = plan(calculations=[calc_request("trade_value_delta")])
        out = DecisionOutput(
            decision_type=DecisionType.TRADE_EVALUATION.value,
            action=DecisionAction.ACCEPT.value,
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            actionable_now=True,
            recommendation_complete=True,
            confidence="high",
        )

        validation = validate(p, calcs=calc_packet(p, complete=False), out=out)

        self.assertIn(validation.validation_status, {ValidationStatus.BLOCKED.value, ValidationStatus.REJECTED.value})
        self.assertFalse(validation.approved_for_action)

    def test_estimated_calculation_lowers_confidence(self):
        p = plan(calculations=[calc_request("trade_value_delta")])

        validation = validate(p, calcs=calc_packet(p, results=[result(estimated=True)]))

        self.assertIn(validation.validation_status, {ValidationStatus.APPROVED_WITH_WARNINGS.value, ValidationStatus.APPROVED.value})
        self.assertNotEqual(validation.confidence_after_validation, "high")

    def test_hard_constraint_violation_rejects(self):
        p = plan()
        obj = objective(constraints=[hard_constraint("do_not_trade_player", "Player p1")])
        out = DecisionOutput(
            decision_type=DecisionType.TRADE_EVALUATION.value,
            action=DecisionAction.ACCEPT.value,
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            evidence_refs=["p1"],
            actionable_now=True,
            recommendation_complete=True,
            confidence="high",
        )

        validation = validate(p, obj=obj, out=out)

        self.assertEqual(validation.validation_status, ValidationStatus.REJECTED.value)

    def test_factual_response_is_not_applicable_and_not_actionable(self):
        p = plan(PlanType.FACTUAL_LOOKUP.value, "factual_lookup_engine")
        interp = interpreted(Intent.DATA_LOOKUP.value)
        obj = objective(Goal.FACTUAL_LOOKUP.value)
        out = decision(p, interp=interp, obj=obj, calcs=calc_packet(p))

        validation = validate(p, interp=interp, obj=obj, calcs=calc_packet(p), out=out)

        self.assertEqual(validation.validation_status, ValidationStatus.NOT_APPLICABLE.value)
        self.assertTrue(validation.approved_for_explanation)
        self.assertFalse(validation.approved_for_action)

    def test_lineup_start_requires_lineup_evidence(self):
        p = plan(PlanType.LINEUP.value, "lineup_engine")
        out = DecisionOutput(
            decision_type=DecisionType.LINEUP_DECISION.value,
            action=DecisionAction.START.value,
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            evidence_refs=["p1"],
            actionable_now=True,
            recommendation_complete=True,
            confidence="high",
        )

        validation = validate(p, out=out, calcs=calc_packet(p, results=[result("lineup_projection", {"projected_points": 15})]))

        self.assertEqual(validation.validation_status, ValidationStatus.REJECTED.value)

    def test_free_agent_acquire_requires_verified_availability(self):
        p = plan(PlanType.FREE_AGENT.value, "free_agent_engine")
        out = DecisionOutput(
            decision_type=DecisionType.FREE_AGENT_RECOMMENDATION.value,
            action=DecisionAction.ACQUIRE.value,
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            evidence_refs=["p2"],
            actionable_now=False,
            recommendation_complete=True,
            confidence="medium",
        )
        ev = evidence(p, players=[player("p2", free=True)], free_agents=[FreeAgentEvidence("p2", "Player p2", "RB", False, None, {}, {})])

        validation = validate(p, ev=ev, out=out, calcs=calc_packet(p))

        self.assertEqual(validation.validation_status, ValidationStatus.REJECTED.value)

    def test_contract_extend_requires_extension_terms(self):
        p = plan(PlanType.CONTRACT.value, "contract_engine")
        out = DecisionOutput(
            decision_type=DecisionType.CONTRACT_DECISION.value,
            action=DecisionAction.EXTEND.value,
            recommendation_status=RecommendationStatus.RECOMMENDED.value,
            evidence_refs=["p1"],
            actionable_now=False,
            recommendation_complete=True,
            confidence="medium",
        )
        ev = evidence(p, contracts=[ContractEvidence("p1", "team-1", 2026, 10, 2, "Active", {}, {}, ["contracts"])])

        validation = validate(p, ev=ev, out=out, calcs=calc_packet(p))

        self.assertEqual(validation.validation_status, ValidationStatus.REJECTED.value)

    def test_serialization_excludes_raw_and_secret_fields(self):
        p = plan()
        validation = validate(p)

        packet = build_validation_packet(validation)
        text = repr(packet)

        self.assertIn("validation_status", packet)
        self.assertIn("approved_for_explanation", packet)
        self.assertNotIn("raw_row", text)
        self.assertNotIn("service_key", text)
        self.assertNotIn("chain_of_thought", text)

    def test_openai_service_accepts_validation_packet_and_preserves_raw_question(self):
        service = importlib.import_module("gm_assistant.openai_service")
        p = plan()
        validation = validate(p)

        messages = service._build_initial_messages(
            "Raw user question?",
            [],
            state(),
            interpreted(),
            objective(),
            p,
            evidence(p),
            rules(p),
            calc_packet(p, results=[result()]),
            decision(p),
            validation,
        )

        self.assertEqual(messages[-1]["content"], "Raw user question?")
        self.assertTrue(any("Structured deterministic recommendation validation" in message["content"] for message in messages))
        self.assertTrue(any("approved_for_action" in message["content"] for message in messages))


if __name__ == "__main__":
    unittest.main()
