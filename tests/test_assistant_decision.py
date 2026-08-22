from __future__ import annotations

import sys
import types
import unittest
from dataclasses import replace

from gm_assistant.calculations import (
    CalculationPacket,
    CalculationResult,
    CalculationStatus,
    UnresolvedCalculation,
)
from gm_assistant.conversation_state import ConversationState
from gm_assistant.decision import (
    DecisionAction,
    DecisionType,
    RecommendationStatus,
    build_decision_output,
    build_decision_packet,
)
from gm_assistant.evidence import (
    CapEvidence,
    ContractEvidence,
    DraftPickEvidence,
    EvidenceContextRef,
    EvidencePacket,
    FreeAgentEvidence,
    LineupEvidence,
    PlayerEvidence,
    TeamEvidence,
)
from gm_assistant.interpretation import AssetRef, Intent, InterpretedQuestion
from gm_assistant.objective import Goal, ObjectiveConstraint, OwnerObjective, StrategicConflict
from gm_assistant.planning import CalculationRequest, DecisionPlan, PlanType, RetrievalRequest
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE
from gm_assistant.rules import RuleCondition, RuleViolation, RulesEvaluation


auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)


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
        "conversation_id": ctx.conversation_id or "conversation-1",
        "user_id": ctx.user_id,
        "league_id": ctx.league_id,
        "league_team_id": ctx.league_team_id,
    }
    data.update(overrides)
    return ConversationState(**data)


def interpreted(*, intent=Intent.TRADE_EVALUATION.value, assets=None):
    return InterpretedQuestion("Should I do this?", intent, included_assets=list(assets or []), confidence="high")


def objective(goal=Goal.CONSTRUCT_TRANSACTION.value, *, constraints=None, conflicts=None, risk="balanced"):
    return OwnerObjective(
        request_goal=goal,
        active_strategic_goal=goal if goal not in {Goal.FACTUAL_LOOKUP.value, Goal.UNDERSTAND_RULES.value} else None,
        primary_goal=goal,
        risk_tolerance=risk,
        non_negotiables=list(constraints or []),
        strategic_conflicts=list(conflicts or []),
        confidence="high",
    )


def hard_constraint(kind, value=True):
    return ObjectiveConstraint(kind, value, "explicit_current_message", True, "current_request")


def plan(plan_type=PlanType.TRADE_EVALUATION.value, engine="trade_evaluation_engine", *, calculations=None, retrievals=None, ready=True):
    return DecisionPlan(
        plan_type=plan_type,
        request_goal=Goal.CONSTRUCT_TRANSACTION.value,
        active_strategic_goal=Goal.CONSTRUCT_TRANSACTION.value,
        decision_engine=engine,
        response_mode="structured_evaluation",
        retrieval_requests=list(retrievals or []),
        calculation_requests=list(calculations or []),
        ready_for_execution=ready,
        confidence="high",
    )


def calc_request(kind):
    return CalculationRequest(kind, True, f"Calculate {kind}.", [], kind)


def evidence(for_plan, ctx=None, **parts):
    ctx = ctx or context()
    return EvidencePacket(
        request_context_ref=EvidenceContextRef(ctx.user_id, ctx.league_id, ctx.league_team_id, ctx.conversation_id, ctx.current_season, [ctx.requested_season]),
        plan_type=for_plan.plan_type,
        decision_engine=for_plan.decision_engine,
        team_evidence=list(parts.get("teams") or [team()]),
        player_evidence=list(parts.get("players") or []),
        contract_evidence=list(parts.get("contracts") or []),
        cap_evidence=list(parts.get("caps") or []),
        draft_pick_evidence=list(parts.get("picks") or []),
        lineup_evidence=list(parts.get("lineups") or []),
        free_agent_evidence=list(parts.get("free_agents") or []),
        required_evidence_complete=parts.get("required_complete", True),
        reduced_mode=parts.get("reduced", False),
        execution_status=parts.get("execution_status", "complete"),
    )


def rules(for_plan, *, status="not_applicable", violations=None, conditions=None, unresolved=None):
    return RulesEvaluation(
        for_plan.plan_type,
        overall_status=status,
        violations=list(violations or []),
        conditions=list(conditions or []),
        unresolved_rules=list(unresolved or []),
        legal_now=False if status == "illegal" else True if status == "legal" else None,
        conditionally_legal=status == "conditionally_legal",
        blocking_violation=status == "illegal",
        rules_complete=status not in {"blocked", "failed", "unverifiable"},
        reduced_mode=status in {"conditionally_legal", "unverifiable"},
        confidence="low" if status == "unverifiable" else "medium" if status == "conditionally_legal" else "high",
    )


def calc_packet(for_plan, *, results=None, unresolved=None, complete=True, reduced=False, status="complete"):
    return CalculationPacket(
        for_plan.plan_type,
        for_plan.decision_engine,
        results=list(results or []),
        unresolved_calculations=list(unresolved or []),
        required_calculations_complete=complete,
        reduced_mode=reduced,
        execution_status=status,
        confidence="low" if not complete else "medium" if reduced else "high",
    )


def result(kind, value, *, estimated=False, output=None):
    return CalculationResult(
        calculation_type=kind,
        status=CalculationStatus.ESTIMATED.value if estimated else CalculationStatus.SUCCESS.value,
        value=value,
        unit=None,
        output_key=output or kind,
        method="test_method",
        formula_version="test_formula",
        exact=not estimated,
        estimated=estimated,
        required=True,
        confidence="medium" if estimated else "high",
    )


def team(team_id="team-1", *, brain=None):
    return TeamEvidence(
        team_id,
        "Team One",
        "Owner One",
        ["p1", "p2"],
        {},
        brain if brain is not None else {"team_direction": "CONTEND_NOW", "championship_window_score": 82, "position_needs": ["RB"], "position_strengths": ["WR"]},
        {},
        ["team_brain"],
    )


def player(pid="p1", *, team_id="team-1", value=70, age=24, risk=None):
    profile = {"asset_score": value}
    if risk:
        profile["risk"] = risk
    return PlayerEvidence(pid, f"Player {pid}", "WR", "NYJ", age, 2, "Active", team_id, False, profile, {"overall_value_score": value}, ["player_strategic_profiles"])


def contract(pid="p1", *, team_id="team-1", salary=10, years=2):
    return ContractEvidence(pid, team_id, 2026, salary, years, "Active", {}, {"salary": salary}, ["contracts"])


def decision(for_plan, *, ctx=None, conv=None, interp=None, obj=None, ev=None, rule_eval=None, calcs=None):
    ctx = ctx or context()
    conv = conv if conv is not None else state(ctx)
    interp = interp or interpreted()
    obj = obj or objective()
    ev = ev or evidence(for_plan, ctx)
    rule_eval = rule_eval or rules(for_plan)
    calcs = calcs or calc_packet(for_plan)
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


class ScopeRegistryAndGatingTest(unittest.TestCase):
    def test_matching_stage_scope_succeeds_and_registry_selects_trade_engine(self):
        p = plan(calculations=[calc_request("trade_value_delta")])
        out = decision(p, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 8})]))
        self.assertEqual(out.decision_type, DecisionType.TRADE_EVALUATION.value)
        self.assertEqual(out.action, DecisionAction.ACCEPT.value)

    def test_mismatched_user_league_team_conversation_blocks(self):
        p = plan()
        base = state()
        cases = [
            replace(base, user_id="user-x"),
            replace(base, league_id="league-x"),
            replace(base, league_team_id="team-x"),
            replace(base, conversation_id="conversation-x"),
        ]
        for conv in cases:
            with self.subTest(conv=conv):
                self.assertEqual(decision(p, conv=conv).recommendation_status, RecommendationStatus.BLOCKED.value)

    def test_mismatched_evidence_rules_or_calculations_block(self):
        p = plan()
        ev = replace(evidence(p), plan_type="other")
        self.assertEqual(decision(p, ev=ev).recommendation_status, RecommendationStatus.BLOCKED.value)
        self.assertEqual(decision(p, rule_eval=RulesEvaluation("other", "not_applicable")).recommendation_status, RecommendationStatus.BLOCKED.value)
        self.assertEqual(decision(p, calcs=CalculationPacket("other", p.decision_engine)).recommendation_status, RecommendationStatus.BLOCKED.value)

    def test_commissioner_scope_does_not_override_illegal_or_ownership(self):
        p = plan()
        commissioner = context(role="commissioner")
        violation = RuleViolation("asset_not_owned", "asset_ownership", "Asset is not owned.", ["p1"], True)
        out = decision(p, ctx=commissioner, conv=state(commissioner), rule_eval=rules(p, status="illegal", violations=[violation]))
        self.assertEqual(out.action, DecisionAction.REJECT.value)

    def test_unsupported_engine_handled_safely(self):
        p = plan(plan_type="custom_plan", engine="mystery_engine")
        out = decision(p)
        self.assertEqual(out.recommendation_status, RecommendationStatus.UNSUPPORTED.value)

    def test_incomplete_required_calculation_blocks_trade_value_decision(self):
        p = plan()
        calcs = calc_packet(p, unresolved=[UnresolvedCalculation("trade_value_delta", "missing p2 value", True, ["stored_player_value"], ["p2"])], complete=False)
        out = decision(p, calcs=calcs)
        self.assertEqual(out.recommendation_status, RecommendationStatus.INSUFFICIENT_INFORMATION.value)
        self.assertFalse(out.actionable_now)

    def test_conditional_and_unverifiable_rules_preserved(self):
        p = plan()
        cond = RuleCondition("post_trade_cap", "Cap must be confirmed.", None, True, ["cap"])
        positive = calc_packet(p, results=[result("trade_value_delta", {"value_delta": 8})])
        out = decision(p, rule_eval=rules(p, status="conditionally_legal", conditions=[cond]), calcs=positive)
        self.assertEqual(out.recommendation_status, RecommendationStatus.RECOMMENDED_WITH_CONDITIONS.value)
        self.assertTrue(out.conditions)
        unverifiable = decision(p, rule_eval=rules(p, status="unverifiable"), calcs=positive)
        self.assertFalse(unverifiable.actionable_now)

    def test_factual_rules_general_and_unsupported_responses(self):
        factual = plan(PlanType.FACTUAL_LOOKUP.value, "factual_lookup_engine")
        self.assertEqual(decision(factual).recommendation_status, RecommendationStatus.NOT_APPLICABLE.value)
        rules_plan = plan(PlanType.RULES_LOOKUP.value, "rules_explanation_engine")
        illegal = RuleViolation("cap", "salary_cap", "Over cap.", [], True)
        self.assertEqual(decision(rules_plan, rule_eval=rules(rules_plan, status="illegal", violations=[illegal])).action, DecisionAction.REJECT.value)
        general = plan(PlanType.GENERAL_CONVERSATION.value, "conversation_engine")
        self.assertEqual(decision(general).decision_type, DecisionType.GENERAL_CONVERSATION.value)
        unsupported = plan(PlanType.UNSUPPORTED.value, "unsupported_engine")
        self.assertEqual(decision(unsupported).recommendation_status, RecommendationStatus.UNSUPPORTED.value)


class ObjectivePlayerRosterTest(unittest.TestCase):
    def test_hard_constraint_rejects_first_round_pick_before_scoring(self):
        p = plan()
        interp = interpreted(assets=[AssetRef("draft_pick", "pick-1", "2026 first", "outgoing", season=2026)])
        obj = objective(constraints=[hard_constraint("do_not_trade_first_round_pick")])
        out = decision(p, interp=interp, obj=obj, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 30})]))
        self.assertEqual(out.action, DecisionAction.REJECT.value)
        self.assertTrue(out.rejected_options)

    def test_player_evaluation_retain_sell_acquire_avoid_monitor(self):
        p = plan(PlanType.PLAYER_EVALUATION.value, "player_evaluation_engine")
        retain = decision(p, ev=evidence(p, players=[player("p1", value=80)]), calcs=calc_packet(p, results=[result("contract_efficiency", {"p1": 6})]))
        self.assertEqual(retain.action, DecisionAction.RETAIN.value)
        sell = decision(p, ev=evidence(p, players=[player("p1", value=80)]), calcs=calc_packet(p, results=[result("contract_efficiency", {"p1": 1})]))
        self.assertEqual(sell.action, DecisionAction.SELL.value)
        acquire = decision(p, ev=evidence(p, players=[player("p2", team_id="team-2", value=75)]), calcs=calc_packet(p, results=[result("player_value", {"p2": 75})]))
        self.assertEqual(acquire.action, DecisionAction.ACQUIRE.value)
        avoid = decision(p, ev=evidence(p, players=[player("p3", team_id="team-2", value=30)]), calcs=calc_packet(p, results=[result("player_value", {"p3": 30})]))
        self.assertEqual(avoid.action, DecisionAction.AVOID.value)
        missing = replace(player("p4", value=70), league_relative_value={}, strategic_profile={})
        monitor = decision(p, ev=evidence(p, players=[missing]), calcs=calc_packet(p))
        self.assertEqual(monitor.recommendation_status, RecommendationStatus.INSUFFICIENT_INFORMATION.value)

    def test_player_comparison_prefer_a_b_or_no_clear_preference(self):
        p = plan(PlanType.PLAYER_COMPARISON.value, "player_comparison_engine")
        prefer_a = decision(p, ev=evidence(p, players=[player("a", value=80), player("b", value=55)]))
        self.assertEqual(prefer_a.action, DecisionAction.PREFER_PLAYER_A.value)
        prefer_b = decision(p, ev=evidence(p, players=[player("a", value=50), player("b", value=80)]))
        self.assertEqual(prefer_b.action, DecisionAction.PREFER_PLAYER_B.value)
        close = decision(p, ev=evidence(p, players=[player("a", value=60), player("b", value=61)]))
        self.assertEqual(close.action, DecisionAction.NO_CLEAR_PREFERENCE.value)

    def test_roster_strategy_respects_explicit_owner_goal_and_surfaces_conflict(self):
        p = plan(PlanType.ROSTER_EVALUATION.value, "roster_evaluation_engine")
        conflict = StrategicConflict("weak_team", Goal.CONTEND_THIS_SEASON.value, "team_brain_rebuild", "medium", "Team-brain direction conflicts with owner goal.")
        out = decision(p, obj=objective(Goal.CONTEND_THIS_SEASON.value, conflicts=[conflict]), ev=evidence(p, teams=[team(brain={"team_direction": "REBUILD"})]))
        self.assertEqual(out.action, DecisionAction.CONTEND.value)
        self.assertTrue(out.risks)
        self.assertTrue(out.reduced_mode)

    def test_roster_strategy_contend_rebuild_retool_and_balanced(self):
        p = plan(PlanType.ROSTER_EVALUATION.value, "roster_evaluation_engine")
        self.assertEqual(decision(p, obj=objective(Goal.REBUILD.value)).action, DecisionAction.REBUILD.value)
        self.assertEqual(decision(p, obj=objective(Goal.RETOOL.value)).action, DecisionAction.RETOOL.value)
        self.assertEqual(decision(p, obj=objective(Goal.WIN_NOW.value)).action, DecisionAction.CONTEND.value)
        self.assertEqual(decision(p, obj=objective(Goal.IMPROVE_DEPTH.value), ev=evidence(p, teams=[team(brain={})])).action, DecisionAction.STAY_BALANCED.value)


class TradeDraftContractLineupTest(unittest.TestCase):
    def test_trade_accept_reject_counter_and_illegal_override(self):
        p = plan()
        accept = decision(p, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 12})]))
        self.assertEqual(accept.action, DecisionAction.ACCEPT.value)
        reject = decision(p, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": -12})]))
        self.assertEqual(reject.action, DecisionAction.REJECT.value)
        counter = decision(p, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 2})]))
        self.assertEqual(counter.action, DecisionAction.COUNTER.value)
        violation = RuleViolation("roster", "roster_size", "Illegal roster.", [], True)
        illegal = decision(p, rule_eval=rules(p, status="illegal", violations=[violation]), calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 50})]))
        self.assertEqual(illegal.action, DecisionAction.REJECT.value)

    def test_trade_cap_disadvantage_rejects_and_partial_value_never_becomes_zero(self):
        p = plan()
        cap_bad = decision(p, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 8}), result("trade_cap_delta", -2)]))
        self.assertEqual(cap_bad.action, DecisionAction.REJECT.value)
        partial = calc_packet(p, results=[result("trade_value_delta", {"value_delta": 8, "complete": False})], unresolved=[UnresolvedCalculation("trade_value_delta", "missing", True, ["stored_player_value"], ["p2"])], complete=False)
        self.assertEqual(decision(p, calcs=partial).recommendation_status, RecommendationStatus.INSUFFICIENT_INFORMATION.value)

    def test_trade_discovery_verified_candidates_only_and_bounded(self):
        p = plan(PlanType.TRADE_DISCOVERY.value, "trade_discovery_engine")
        players = [player(f"p{i}", team_id="team-2", value=70 - i) for i in range(14)] + [player("mine", team_id="team-1", value=99)]
        out = decision(p, ev=evidence(p, players=players))
        self.assertEqual(out.action, DecisionAction.PURSUE.value)
        self.assertLessEqual(1 + len(out.alternatives), 5)
        self.assertNotIn("mine", str(out.to_packet()))

    def test_trade_construction_no_acceptance_probability_claim(self):
        p = plan(PlanType.TRADE_CONSTRUCTION.value, "trade_construction_engine")
        out = decision(p, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 8})]))
        self.assertEqual(out.action, DecisionAction.COUNTER.value)
        self.assertIn("acceptance_probability_unknown", [c.condition_type for c in out.conditions])

    def test_draft_recommendation_and_pick_evaluation_do_not_invent_values(self):
        draft = plan(PlanType.DRAFT_RECOMMENDATION.value, "draft_recommendation_engine")
        no_pick = decision(draft, ev=evidence(draft, picks=[]))
        self.assertEqual(no_pick.recommendation_status, RecommendationStatus.INSUFFICIENT_INFORMATION.value)
        with_pick = decision(draft, ev=evidence(draft, picks=[DraftPickEvidence("1.03", 2026, 1, 3, "team-1", "team-1", "available", True, ["draft_picks"])], players=[player("rookie", team_id=None, value=80)]))
        self.assertEqual(with_pick.action, DecisionAction.DRAFT_PLAYER.value)
        pick_eval = plan(PlanType.DRAFT_PICK_EVALUATION.value, "draft_pick_evaluation_engine")
        unresolved = calc_packet(pick_eval, unresolved=[UnresolvedCalculation("pick_value", "missing", True, ["verified_pick_value"])], complete=False)
        self.assertEqual(decision(pick_eval, ev=evidence(pick_eval, picks=[DraftPickEvidence("2028_1", 2028, 1, None, "team-1", "team-1", "available", True, ["draft_picks"])]), calcs=unresolved).recommendation_status, RecommendationStatus.INSUFFICIENT_INFORMATION.value)

    def test_free_agent_requires_verified_availability(self):
        p = plan(PlanType.FREE_AGENT.value, "free_agent_engine")
        missing = decision(p, ev=evidence(p, free_agents=[]))
        self.assertEqual(missing.recommendation_status, RecommendationStatus.INSUFFICIENT_INFORMATION.value)
        out = decision(p, ev=evidence(p, players=[player("fa", team_id=None, value=66)], free_agents=[FreeAgentEvidence("fa", "FA", "RB", True, "free_agents", {}, {})]))
        self.assertEqual(out.action, DecisionAction.ACQUIRE.value)

    def test_contract_request_terms_release_and_no_invented_extension(self):
        p = plan(PlanType.CONTRACT.value, "contract_engine")
        request_terms = decision(p, calcs=calc_packet(p, unresolved=[UnresolvedCalculation("extension_schedule", "missing terms", True, ["terms"])], complete=False))
        self.assertEqual(request_terms.action, DecisionAction.REQUEST_MORE_INFORMATION.value)
        release = decision(p, calcs=calc_packet(p, results=[result("release_savings", 4)]))
        self.assertEqual(release.action, DecisionAction.RELEASE.value)

    def test_salary_cap_strategy_uses_savings_calculations_only(self):
        p = plan(PlanType.SALARY_CAP.value, "salary_cap_engine")
        missing = decision(p, calcs=calc_packet(p, unresolved=[UnresolvedCalculation("release_savings", "missing", True, ["release_savings"])], complete=False))
        self.assertEqual(missing.recommendation_status, RecommendationStatus.INSUFFICIENT_INFORMATION.value)
        out = decision(p, obj=objective(Goal.PRESERVE_CAP_FLEXIBILITY.value), calcs=calc_packet(p, results=[result("release_savings", 5)]))
        self.assertEqual(out.action, DecisionAction.PRESERVE_FLEXIBILITY.value)

    def test_lineup_start_requires_projection_and_no_fabricated_points(self):
        p = plan(PlanType.LINEUP.value, "lineup_engine")
        missing = decision(p, calcs=calc_packet(p, unresolved=[UnresolvedCalculation("lineup_projection", "missing", True, ["projections"])], complete=False))
        self.assertEqual(missing.recommendation_status, RecommendationStatus.INSUFFICIENT_INFORMATION.value)
        projected = decision(p, ev=evidence(p, lineups=[LineupEvidence("team-1", 2026, 1, [], [], {}, {}, {"projected_points": 12}, ["lineup"])]), calcs=calc_packet(p, results=[result("lineup_projection", {"projected_points": 12}, estimated=True)]))
        self.assertEqual(projected.action, DecisionAction.START.value)
        self.assertEqual(projected.confidence, "medium")

    def test_roster_move_and_long_term_outputs_are_structured_not_executed(self):
        move = plan(PlanType.ROSTER_MOVE.value, "roster_move_engine")
        out = decision(move, obj=objective(Goal.REDUCE_SALARY.value), calcs=calc_packet(move, results=[result("post_transaction_roster_count", {"post_count": 21})]))
        self.assertEqual(out.action, DecisionAction.RELEASE.value)
        self.assertFalse(out.actionable_now)
        long_term = plan(PlanType.LONG_TERM_PLANNING.value, "long_term_planning_engine")
        lt = decision(long_term)
        self.assertEqual(lt.decision_type, DecisionType.LONG_TERM_PLAN.value)
        self.assertGreaterEqual(len(lt.alternatives), 1)


class SerializationAndOpenAITest(unittest.TestCase):
    def test_serialization_excludes_raw_rows_provider_internals_and_chain_of_thought(self):
        p = plan()
        out = decision(p, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 8, "raw_row": {"secret": True}, "chain_of_thought": "nope"})]))
        payload = build_decision_packet(out)
        text = str(payload)
        self.assertIn("primary_recommendation", payload)
        self.assertNotIn("raw_row", text)
        self.assertNotIn("chain_of_thought", text)
        self.assertNotIn("service_key", text)

    def test_openai_service_works_with_and_without_decision_packet_and_question_final(self):
        from gm_assistant.openai_service import _build_initial_messages

        p = plan()
        out = decision(p, calcs=calc_packet(p, results=[result("trade_value_delta", {"value_delta": 8})]))
        without = _build_initial_messages("Question?", [], None, None, None, p, evidence(p), rules(p), calc_packet(p), None)
        with_decision = _build_initial_messages("Question?", [], None, None, None, p, evidence(p), rules(p), calc_packet(p), out)
        self.assertEqual(without[-1], {"role": "user", "content": "Question?"})
        self.assertIn("Structured deterministic decision", with_decision[-2]["content"])
        self.assertIn("authoritative", with_decision[-2]["content"])
        self.assertEqual(with_decision[-1], {"role": "user", "content": "Question?"})

    def test_page_compiles_with_stage9_flow(self):
        with open("/Users/tommybruggeman/Desktop/Legacy App/pages/05_GM_Assistant.py") as handle:
            source = handle.read()
        self.assertIn("AssistantRuntime", source)
        self.assertIn("AssistantRuntimeInput", source)
        self.assertNotIn("build_decision_output(", source)


if __name__ == "__main__":
    unittest.main()
