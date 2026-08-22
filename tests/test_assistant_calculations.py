from __future__ import annotations

import sys
import types
import unittest
from dataclasses import replace

from gm_assistant.calculations import (
    CalculationExecutionStatus,
    CalculationStatus,
    CalculationType,
    build_calculation_packet,
    build_calculation_packet_payload,
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
from gm_assistant.objective import Goal, OwnerObjective
from gm_assistant.planning import CalculationRequest, DecisionPlan, PlanType, RetrievalRequest
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE
from gm_assistant.rules import OverallStatus, RequiredRuleCalculation, RulesEvaluation


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


def interpreted(*, assets=None, intent=Intent.TRADE_EVALUATION.value):
    return InterpretedQuestion("Calculate this.", intent, included_assets=list(assets or []), confidence="high")


def objective(goal=Goal.CONSTRUCT_TRANSACTION.value):
    return OwnerObjective(goal, "contend_this_season", goal, confidence="high")


def calc(calculation_type, *, required=True, output_key=None):
    return CalculationRequest(calculation_type, required, f"Calculate {calculation_type}.", [], output_key or calculation_type)


def plan(*calculations, plan_type=PlanType.TRADE_EVALUATION.value, ready=True, retrievals=None):
    return DecisionPlan(
        plan_type=plan_type,
        request_goal=Goal.CONSTRUCT_TRANSACTION.value,
        active_strategic_goal="contend_this_season",
        decision_engine="trade_evaluation_engine",
        response_mode="structured_evaluation",
        retrieval_requests=list(retrievals or []),
        calculation_requests=list(calculations),
        ready_for_execution=ready,
        confidence="high",
    )


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
        execution_status=parts.get("execution_status", "complete"),
    )


def rules(for_plan, *, required=None, status=OverallStatus.NOT_APPLICABLE.value):
    return RulesEvaluation(for_plan.plan_type, overall_status=status, required_calculations=list(required or []))


def packet(for_plan, *, ctx=None, interp=None, obj=None, ev=None, rule_eval=None):
    ctx = ctx or context()
    interp = interp or interpreted()
    obj = obj or objective()
    ev = ev or evidence(for_plan, ctx)
    rule_eval = rule_eval or rules(for_plan)
    return build_calculation_packet(
        context=ctx,
        interpreted_question=interp,
        owner_objective=obj,
        decision_plan=for_plan,
        evidence_packet=ev,
        rules_evaluation=rule_eval,
    )


def team(team_id="team-1", players=None, *, brain=None, summary=None):
    brain = {"team_direction": "CONTEND_NOW", "championship_window_score": 88, "position_needs": ["RB"], "position_strengths": ["WR"]} if brain is None else brain
    summary = {"taxi_count": 1, "ir_count": 1, "bench_count": 1} if summary is None else summary
    return TeamEvidence(
        league_team_id=team_id,
        team_name="Team One",
        owner_name="Owner One",
        roster_player_ids=list(players if players is not None else ["p1", "p2", "p3"]),
        roster_summary=summary,
        team_brain_summary=brain,
        positional_summary={},
        data_sources=["team_roster_state"],
    )


def cap(team_id="team-1", *, available=12, salary_cap=225, active=210, dead=3, season=2026):
    return CapEvidence(team_id, season, salary_cap, active, dead, available, {}, {}, ["v_team_caps"])


def player(player_id="p1", *, team_id="team-1", pos="WR", age=24.5, value=70):
    return PlayerEvidence(
        player_id,
        f"Player {player_id}",
        pos,
        "NYJ",
        age,
        2,
        "Active",
        team_id,
        False,
        {"asset_score": value},
        {"overall_value_score": value},
        ["player_strategic_profiles"],
    )


def contract(player_id="p1", *, team_id="team-1", salary=10, years=2, status="Active", terms=None):
    return ContractEvidence(player_id, team_id, 2026, salary, years, status, {}, terms or {"salary": salary, "contract_years_left": years}, ["contracts"])


def pick(pick_id="pick-1", *, season=2026, round_number=1, owner="team-1"):
    return DraftPickEvidence(pick_id, season, round_number, 3, "team-1", owner, "available", True, ["draft_picks"])


class ScopeAndPlanningTest(unittest.TestCase):
    def test_matching_stage_scope_succeeds(self):
        p = plan(calc(CalculationType.CURRENT_ROSTER_COUNT.value))
        result = packet(p)
        self.assertEqual(result.execution_status, CalculationExecutionStatus.COMPLETE.value)

    def test_mismatched_user_league_team_conversation_evidence_blocks(self):
        p = plan(calc(CalculationType.CURRENT_ROSTER_COUNT.value))
        base = evidence(p)
        cases = [
            replace(base, request_context_ref=replace(base.request_context_ref, user_id="user-x")),
            replace(base, request_context_ref=replace(base.request_context_ref, league_id="league-x")),
            replace(base, request_context_ref=replace(base.request_context_ref, league_team_id="team-x")),
            replace(base, request_context_ref=replace(base.request_context_ref, conversation_id="conversation-x")),
        ]
        for ev in cases:
            with self.subTest(ev=ev.request_context_ref):
                self.assertEqual(packet(p, ev=ev).execution_status, CalculationExecutionStatus.BLOCKED.value)

    def test_mismatched_rules_evaluation_blocks(self):
        p = plan(calc(CalculationType.CURRENT_ROSTER_COUNT.value))
        result = packet(p, rule_eval=RulesEvaluation("other_plan", OverallStatus.NOT_APPLICABLE.value))
        self.assertEqual(result.execution_status, CalculationExecutionStatus.BLOCKED.value)

    def test_cross_scope_evidence_blocks_and_commissioner_does_not_bypass(self):
        p = plan(calc(CalculationType.CURRENT_ROSTER_COUNT.value))
        commissioner = context(role="commissioner", permission_scopes=(TEAM_ADVICE, LEAGUE_PUBLIC_READ, "league_admin"))
        ev = evidence(p, ctx=commissioner, teams=[team("team-2")])
        result = packet(p, ctx=commissioner, ev=ev)
        self.assertEqual(result.execution_status, CalculationExecutionStatus.BLOCKED.value)

    def test_cross_contract_pick_lineup_evidence_blocks(self):
        non_transaction_plan = plan(calc("current_roster_count"), plan_type=PlanType.ROSTER_EVALUATION.value)
        for ev in [
            evidence(non_transaction_plan, contracts=[contract(team_id="team-2")]),
            evidence(non_transaction_plan, picks=[pick(owner="team-2")]),
            evidence(non_transaction_plan, lineups=[LineupEvidence("team-2", 2026, 1, [], [], {}, {}, {}, ["lineup"])]),
        ]:
            result = packet(non_transaction_plan, ev=ev)
            self.assertEqual(result.execution_status, CalculationExecutionStatus.BLOCKED.value)

    def test_only_requested_calculations_execute_and_duplicates_dedupe(self):
        p = plan(calc("current_roster_count"), calc("current_roster_count"), calc("available_cap", required=False))
        result = packet(p, ev=evidence(p, caps=[cap()]))
        self.assertEqual([r.calculation_type for r in result.results], ["current_roster_count", "available_cap"])
        self.assertNotIn("trade_value_delta", [r.calculation_type for r in result.results])

    def test_no_calculation_plan_is_not_applicable(self):
        p = plan(plan_type=PlanType.GENERAL_CONVERSATION.value)
        result = packet(p, ev=evidence(p), rule_eval=rules(p))
        self.assertEqual(result.execution_status, CalculationExecutionStatus.NOT_APPLICABLE.value)

    def test_blocked_plan_does_not_calculate(self):
        p = replace(plan(calc("current_roster_count")), ready_for_execution=False)
        self.assertEqual(packet(p).execution_status, CalculationExecutionStatus.BLOCKED.value)

    def test_stage7_required_calculation_executes(self):
        p = plan()
        required = [RequiredRuleCalculation("post_transaction_roster_count", "Needed by rule.", ["team_evidence"], True)]
        interp = interpreted(assets=[AssetRef("player", "p1", "P1", "outgoing"), AssetRef("player", "p2", "P2", "incoming")])
        result = packet(p, interp=interp, rule_eval=rules(p, required=required))
        self.assertEqual(result.results[0].calculation_type, "post_transaction_roster_count")
        self.assertEqual(result.results[0].satisfies_rule_calculation_refs, ["rules_evaluation.required_calculations[0]"])


class CapContractRosterTest(unittest.TestCase):
    def test_current_cap_and_available_cap_use_canonical_evidence(self):
        p = plan(calc("current_cap_total"), calc("available_cap"))
        result = packet(p, ev=evidence(p, caps=[cap(available=12, salary_cap=225, active=210, dead=3)]))
        values = [r.value for r in result.results]
        self.assertIn(12.0, values)
        self.assertTrue(all(r.exact for r in result.results))

    def test_cap_formula_conflict_warns_but_preserves_available_cap(self):
        p = plan(calc("available_cap"))
        result = packet(p, ev=evidence(p, caps=[cap(available=20, salary_cap=225, active=210, dead=3)]))
        self.assertIn("available_cap_conflicts_with_salary_cap_formula", result.warnings)

    def test_post_trade_cap_uses_salary_delta_and_does_not_execute_transaction(self):
        p = plan(calc("cap_impact"))
        interp = interpreted(assets=[AssetRef("player", "p1", "P1", "outgoing"), AssetRef("player", "p2", "P2", "incoming")])
        ev = evidence(p, caps=[cap(available=12)], contracts=[contract("p1", salary=8), contract("p2", team_id="team-2", salary=5)])
        result = packet(p, interp=interp, ev=ev)
        self.assertEqual(result.results[0].value, 15.0)
        self.assertTrue(result.scenario_results)

    def test_missing_trade_salary_is_unresolved_not_zero(self):
        p = plan(calc("cap_impact"))
        interp = interpreted(assets=[AssetRef("player", "p1", "P1", "outgoing"), AssetRef("player", "p2", "P2", "incoming")])
        ev = evidence(p, caps=[cap()], contracts=[contract("p1", salary=8)])
        result = packet(p, interp=interp, ev=ev)
        self.assertFalse(result.required_calculations_complete)
        self.assertIn("p2", result.unresolved_calculations[0].related_entity_ids)

    def test_dead_cap_and_release_savings_use_transaction_engine_v1_formula(self):
        for calc_type in ("dead_cap_impact", "release_savings"):
            p = plan(calc(calc_type))
            result = packet(p, ev=evidence(p, contracts=[contract("p1", salary=10, years=2)]))
            self.assertIn("transaction_engine", result.results[0].method)
            self.assertTrue(result.assumptions)

    def test_extension_schedule_requires_supplied_terms(self):
        p = plan(calc("extension_schedule"))
        missing = packet(p, ev=evidence(p, contracts=[contract("p1")]))
        self.assertFalse(missing.required_calculations_complete)
        supplied = packet(p, ev=evidence(p, contracts=[contract("p1", terms={"proposed_extension_years": 2, "proposed_annual_salary": 12})]))
        self.assertEqual(supplied.results[0].value, {"2028": 12.0, "2029": 12.0})

    def test_contract_years_and_future_commitment(self):
        p = plan(calc("contract_years_remaining"), calc("future_salary_commitment"))
        result = packet(p, ev=evidence(p, contracts=[contract("p1", salary=10, years=2), contract("p2", salary=5, years=1)]))
        self.assertEqual(result.results[0].value, {"p1": 2, "p2": 1})
        self.assertEqual(result.results[1].value, {"2026": 15.0, "2027": 10.0})

    def test_roster_counts_distinguish_zero_from_missing_and_dedupe(self):
        p = plan(calc("current_roster_count"))
        zero = packet(p, ev=evidence(p, teams=[team(players=[], summary={"taxi_count": 0, "ir_count": 0})]))
        self.assertEqual(zero.results[0].value["active_roster_count"], 0)
        deduped = packet(p, ev=evidence(p, teams=[team(players=["p1", "p1", "p2"])]))
        self.assertEqual(deduped.results[0].value["active_roster_count"], 2)

    def test_post_transaction_roster_count_multi_player(self):
        p = plan(calc("post_transaction_roster_count"))
        interp = interpreted(assets=[AssetRef("player", "p1", "P1", "outgoing"), AssetRef("player", "p2", "P2", "incoming"), AssetRef("player", "p3", "P3", "incoming")])
        result = packet(p, interp=interp)
        self.assertEqual(result.results[0].value["post_count"], 4)


class PositionAgeValueTradeDraftTest(unittest.TestCase):
    def test_position_count_normalizes_and_warns_unknown(self):
        p = plan(calc("position_count"))
        result = packet(p, ev=evidence(p, players=[player("p1", pos="DEF"), player("p2", pos="??")]))
        self.assertEqual(result.results[0].value, {"DST": 1})
        self.assertIn("unknown_position_for_p2", result.warnings)

    def test_age_profile_ignores_missing_age_with_coverage_warning(self):
        p = plan(calc("age_profile"))
        result = packet(p, ev=evidence(p, players=[player("p1", age=24), player("p2", age=None)]))
        self.assertEqual(result.results[0].value["average_age"], 24.0)
        self.assertIn("age_coverage_incomplete", result.warnings)

    def test_missing_age_all_unresolved_not_zero(self):
        p = plan(calc("age_profile"))
        result = packet(p, ev=evidence(p, players=[player("p1", age=None)]))
        self.assertFalse(result.results)
        self.assertIn("player_age", result.unresolved_calculations[0].missing_inputs)

    def test_stored_player_value_and_missing_value_partial(self):
        p = plan(calc("player_value"))
        no_value = replace(player("p2", value=70), league_relative_value={}, strategic_profile={})
        result = packet(p, ev=evidence(p, players=[player("p1", value=80), no_value]))
        self.assertEqual(result.results[0].value, {"p1": 80.0})
        self.assertIn("partial_player_value_subtotal", result.warnings)

    def test_trade_value_delta_does_not_treat_missing_as_zero(self):
        p = plan(calc("trade_value_delta"))
        interp = interpreted(assets=[AssetRef("player", "p1", "P1", "outgoing"), AssetRef("player", "p2", "P2", "incoming")])
        no_value = replace(player("p2", team_id="team-2", value=70), league_relative_value={}, strategic_profile={})
        result = packet(p, interp=interp, ev=evidence(p, players=[player("p1", value=60), no_value]))
        self.assertEqual(result.results[0].value["complete"], False)
        self.assertIn("p2", result.unresolved_calculations[0].related_entity_ids)

    def test_trade_value_delta_exact_and_descriptive_fairness_only(self):
        p = plan(calc("trade_fairness"))
        interp = interpreted(assets=[AssetRef("player", "p1", "P1", "outgoing"), AssetRef("player", "p2", "P2", "incoming")])
        result = packet(p, interp=interp, ev=evidence(p, players=[player("p1", value=60), player("p2", team_id="team-2", value=68)]))
        self.assertEqual(result.results[0].value["value_delta"], 8.0)
        self.assertNotIn("accept", str(result.to_payload()).lower())

    def test_trade_realism_is_unresolved_without_canonical_model(self):
        p = plan(calc("trade_realism"))
        result = packet(p)
        self.assertFalse(result.results)
        self.assertIn("No deterministic Stage 8 formula", result.unresolved_calculations[0].explanation)

    def test_draft_capital_counts_premium_picks(self):
        p = plan(calc("draft_capital_total"))
        result = packet(p, ev=evidence(p, picks=[pick("a", round_number=1), pick("b", round_number=2)]))
        self.assertEqual(result.results[0].value["premium_pick_count"], 1)

    def test_pick_value_without_verified_source_unresolved(self):
        p = plan(calc("pick_value"))
        result = packet(p, ev=evidence(p, picks=[pick()]))
        self.assertFalse(result.required_calculations_complete)


class TeamLineupSerializationTest(unittest.TestCase):
    def test_competitive_window_and_team_strength_reuse_team_brain(self):
        p = plan(calc("competitive_window_fit"), calc("team_strength"))
        result = packet(p, ev=evidence(p, teams=[team(brain={"team_direction": "RETOOL", "championship_window_score": 61})]))
        self.assertEqual(result.results[0].value, {"team-1": "RETOOL"})
        self.assertEqual(result.results[1].value, {"team-1": 61})

    def test_position_needs_and_surplus_reuse_team_brain(self):
        p = plan(calc("positional_need"), calc("positional_surplus"))
        result = packet(p)
        self.assertEqual(result.results[0].value, {"team-1": ["RB"]})
        self.assertEqual(result.results[1].value, {"team-1": ["WR"]})

    def test_risk_profile_unresolved_without_structured_team_or_player_source(self):
        p = plan(calc("risk_profile"))
        result = packet(p, ev=evidence(p, teams=[team(brain={}, summary={})]))
        self.assertFalse(result.results)

    def test_lineup_projection_uses_trusted_projection_fields(self):
        p = plan(calc("lineup_projection"), calc("floor_ceiling"))
        line = LineupEvidence("team-1", 2026, 4, ["p1"], ["p2"], {}, {}, {"projected_points": 18.2, "floor": 9, "ceiling": 26}, ["trusted_projection"])
        result = packet(p, ev=evidence(p, lineups=[line]))
        self.assertTrue(all(r.estimated for r in result.results))
        self.assertTrue(result.assumptions)

    def test_lineup_slot_impact_from_stage7_required_calculation(self):
        p = plan()
        line = LineupEvidence("team-1", 2026, 4, ["p1"], ["p2"], {"locked": False}, {}, {}, ["lineup"])
        rule = rules(p, required=[RequiredRuleCalculation("lineup_slot_impact", "Needed.", ["lineup_evidence"], True)])
        result = packet(p, ev=evidence(p, lineups=[line]), rule_eval=rule)
        self.assertEqual(result.results[0].value["starter_count"], 1)

    def test_missing_lineup_projection_unresolved_no_fabricated_points(self):
        p = plan(calc("lineup_projection"))
        line = LineupEvidence("team-1", 2026, 4, ["p1"], ["p2"], {}, {}, {}, ["lineup"])
        result = packet(p, ev=evidence(p, lineups=[line]))
        self.assertFalse(result.results)
        self.assertIn("Projection evidence is unavailable", result.unresolved_calculations[0].explanation)

    def test_packet_payload_excludes_raw_and_internal_objects(self):
        p = plan(calc("current_roster_count"))
        payload = build_calculation_packet_payload(packet(p))
        text = str(payload)
        self.assertIn("results", payload)
        self.assertNotIn("raw_row", text)
        self.assertNotIn("traceback", text)
        self.assertNotIn("service_key", text)

    def test_openai_service_accepts_calculations_and_raw_question_remains_final(self):
        from gm_assistant.openai_service import _build_initial_messages

        p = plan(calc("current_roster_count"))
        calculation_packet = packet(p)
        messages = _build_initial_messages("Question?", [], None, None, None, p, evidence(p), rules(p), calculation_packet)
        self.assertIn("Structured deterministic calculations", messages[-2]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "Question?"})


if __name__ == "__main__":
    unittest.main()
