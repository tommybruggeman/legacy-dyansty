from __future__ import annotations

import importlib
import sys
import types
import unittest
from datetime import datetime
from dataclasses import replace
from zoneinfo import ZoneInfo

from gm_assistant.evidence import (
    CapEvidence,
    ContractEvidence,
    DraftPickEvidence,
    EvidenceContextRef,
    EvidencePacket,
    FreeAgentEvidence,
    LineupEvidence,
    PlayerEvidence,
    RuleEvidence,
    TeamEvidence,
)
from gm_assistant.interpretation import Intent, InterpretedQuestion
from gm_assistant.objective import Goal, OwnerObjective
from gm_assistant.planning import DecisionPlan, PlanType, RetrievalRequest, RuleRequest
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE
from gm_assistant.rules import (
    OverallStatus,
    RuleResultStatus,
    RuleType,
    RulesEvaluation,
    build_rules_packet,
    evaluate_rules,
)


auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)


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
        "timezone": "America/Boise",
        "team_name": "Team One",
        "owner_name": "Owner One",
    }
    data.update(overrides)
    return AssistantRequestContext(**data)


def make_interpreted(intent=Intent.RULES_QUESTION.value):
    return InterpretedQuestion("Can I do this?", intent, confidence="high")


def make_objective(goal=Goal.UNDERSTAND_RULES.value):
    return OwnerObjective(goal, "contend_this_season", goal, confidence="high")


def make_plan(*rules, plan_type=PlanType.RULES_LOOKUP.value, ready=True):
    return DecisionPlan(
        plan_type=plan_type,
        request_goal=Goal.UNDERSTAND_RULES.value,
        active_strategic_goal="contend_this_season",
        decision_engine="rules_explanation_engine",
        response_mode="rules_explanation",
        rule_requests=list(rules),
        ready_for_execution=ready,
        confidence="high",
    )


def with_planned_players(plan, player_ids):
    return replace(
        plan,
        retrieval_requests=[
            RetrievalRequest(
                retrieval_type="asset_ownership",
                scope="league",
                player_ids=list(player_ids),
                seasons=[2026],
                filters={"league_id": "league-1"},
                reason="Verify planned assets.",
            )
        ],
    )


def rr(rule_type, *, season=2026, required=True):
    return RuleRequest(rule_type, season, required, f"Check {rule_type}.")


def player(player_id="p1", *, team_id="team-1", experience=1, status="healthy", profile=None):
    return PlayerEvidence(
        player_id=player_id,
        canonical_name="Player One",
        position="WR",
        nfl_team="NYJ",
        age=24.5,
        experience=experience,
        status=status,
        fantasy_team_id=team_id,
        is_free_agent=None,
        strategic_profile=profile or {},
        league_relative_value={},
        data_sources=["player_strategic_profiles"],
    )


def team(team_id="team-1", players=None):
    return TeamEvidence(
        league_team_id=team_id,
        team_name="Team One",
        owner_name="Owner One",
        roster_player_ids=list(players if players is not None else ["p1", "p2"]),
        roster_summary={},
        team_brain_summary={},
        positional_summary={},
        data_sources=["team_roster_state"],
    )


def cap(*, team_id="team-1", cap_space=12, salary_cap=225, active_salary=210, dead_cap=3, season=2026):
    return CapEvidence(
        league_team_id=team_id,
        season=season,
        salary_cap=salary_cap,
        active_salary=active_salary,
        dead_cap=dead_cap,
        available_cap=cap_space,
        committed_future_salary={},
        source_fields={},
        data_sources=["v_team_caps"],
    )


def rule_evidence(rule_type, value, *, priority=1, season=2026, source="league_rules", verified=True):
    return RuleEvidence(rule_type, season, value, source, priority, verified)


def packet_for(
    plan,
    *,
    context=None,
    rules=None,
    teams=None,
    players=None,
    caps=None,
    picks=None,
    contracts=None,
    lineups=None,
    free_agents=None,
    reduced=False,
    execution_status="complete",
    plan_type=None,
):
    context = context or make_context()
    return EvidencePacket(
        request_context_ref=EvidenceContextRef(context.user_id, context.league_id, context.league_team_id, context.conversation_id, context.current_season, [context.requested_season]),
        plan_type=plan_type or plan.plan_type,
        decision_engine=plan.decision_engine,
        team_evidence=list(teams or []),
        player_evidence=list(players or []),
        cap_evidence=list(caps or []),
        draft_pick_evidence=list(picks or []),
        contract_evidence=list(contracts or []),
        rules_evidence=list(rules or []),
        lineup_evidence=list(lineups or []),
        free_agent_evidence=list(free_agents or []),
        required_evidence_complete=execution_status == "complete",
        reduced_mode=reduced,
        execution_status=execution_status,
    )


def evaluate(plan, *, packet=None, context=None, now=None, **packet_kwargs):
    context = context or make_context()
    packet = packet or packet_for(plan, context=context, **packet_kwargs)
    return evaluate_rules(
        context=context,
        interpreted_question=make_interpreted(),
        owner_objective=make_objective(),
        decision_plan=plan,
        evidence_packet=packet,
        now=now,
    )


class RulesContextScopeTest(unittest.TestCase):
    def test_matching_context_succeeds(self):
        plan = make_plan(rr(RuleType.SALARY_CAP.value))
        result = evaluate(plan, rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap()])
        self.assertEqual(result.overall_status, OverallStatus.LEGAL.value)

    def test_mismatched_user_league_team_conversation_or_plan_blocks(self):
        plan = make_plan(rr(RuleType.SALARY_CAP.value))
        base = packet_for(plan, rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap()])
        cases = [
            replace(base, request_context_ref=replace(base.request_context_ref, user_id="user-x")),
            replace(base, request_context_ref=replace(base.request_context_ref, league_id="league-x")),
            replace(base, request_context_ref=replace(base.request_context_ref, league_team_id="team-x")),
            replace(base, request_context_ref=replace(base.request_context_ref, conversation_id="conversation-x")),
            replace(base, plan_type="other_plan"),
        ]
        for packet in cases:
            with self.subTest(packet=packet.request_context_ref):
                result = evaluate(plan, packet=packet)
                self.assertEqual(result.overall_status, OverallStatus.BLOCKED.value)

    def test_cross_scope_evidence_is_rejected_and_commissioner_does_not_bypass(self):
        plan = make_plan(rr(RuleType.SALARY_CAP.value))
        packet = packet_for(plan, teams=[team("unknown")], rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap()])
        result = evaluate(plan, packet=packet)
        self.assertEqual(result.overall_status, OverallStatus.BLOCKED.value)

        commissioner = make_context(role="commissioner", permission_scopes=(TEAM_ADVICE, LEAGUE_PUBLIC_READ, "league_admin"))
        result = evaluate(plan, context=commissioner, packet=packet_for(plan, context=commissioner, contracts=[ContractEvidence("p1", "team-2", 2026, 1, 1, "active", {}, {}, ["contracts"])]))
        self.assertEqual(result.overall_status, OverallStatus.BLOCKED.value)


class RulesGeneralStatusAndPlanningTest(unittest.TestCase):
    def test_non_legal_factual_request_is_not_applicable(self):
        plan = make_plan(plan_type=PlanType.FACTUAL_LOOKUP.value)
        result = evaluate(plan)
        self.assertEqual(result.overall_status, OverallStatus.NOT_APPLICABLE.value)
        self.assertTrue(result.rules_complete)
        self.assertIsNone(result.legal_now)

    def test_legal_illegal_conditional_unverifiable_and_blocked(self):
        legal = evaluate(make_plan(rr(RuleType.SALARY_CAP.value)), rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap()])
        illegal = evaluate(make_plan(rr(RuleType.SALARY_CAP.value)), rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap(cap_space=-3)])
        conditional = evaluate(make_plan(rr(RuleType.SALARY_CAP.value), plan_type=PlanType.TRADE_EVALUATION.value), rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap()])
        unverifiable = evaluate(make_plan(rr(RuleType.TAXI_SQUAD_ELIGIBILITY.value)), players=[player()])
        blocked = evaluate(make_plan(rr(RuleType.SALARY_CAP.value)), packet=packet_for(make_plan(rr(RuleType.SALARY_CAP.value)), execution_status="blocked"))
        self.assertEqual(legal.overall_status, OverallStatus.LEGAL.value)
        self.assertTrue(legal.legal_now)
        self.assertEqual(illegal.overall_status, OverallStatus.ILLEGAL.value)
        self.assertFalse(illegal.legal_now)
        self.assertEqual(conditional.overall_status, OverallStatus.CONDITIONALLY_LEGAL.value)
        self.assertTrue(conditional.required_calculations)
        self.assertEqual(unverifiable.overall_status, OverallStatus.UNVERIFIABLE.value)
        self.assertEqual(blocked.overall_status, OverallStatus.BLOCKED.value)

    def test_only_requested_rules_evaluate_and_duplicates_dedupe(self):
        plan = make_plan(rr(RuleType.SALARY_CAP.value), rr(RuleType.SALARY_CAP.value), rr(RuleType.TAXI_SQUAD_ELIGIBILITY.value, required=False))
        result = evaluate(plan, rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap()], players=[player()])
        self.assertEqual([r.rule_type for r in result.rule_results], [RuleType.SALARY_CAP.value])
        self.assertEqual(len([u for u in result.unresolved_rules if u.rule_type == RuleType.TAXI_SQUAD_ELIGIBILITY.value]), 1)
        self.assertNotIn(RuleType.TRADE_DEADLINE.value, [r.rule_type for r in result.rule_results])

    def test_no_violation_found_without_source_is_not_legal(self):
        result = evaluate(make_plan(rr(RuleType.LEAGUE_RULE_LOOKUP.value)))
        self.assertEqual(result.overall_status, OverallStatus.UNVERIFIABLE.value)
        self.assertFalse(result.rules_complete)


class RuleSourcePriorityTest(unittest.TestCase):
    def test_structured_source_priority_and_conflicts(self):
        plan = make_plan(rr(RuleType.SALARY_CAP.value))
        result = evaluate(
            plan,
            rules=[
                rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225}, priority=1, source="structured_league_rule"),
                rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 200}, priority=5, source="legacy_ui_text"),
            ],
            caps=[cap()],
        )
        self.assertEqual(result.overall_status, OverallStatus.LEGAL.value)
        self.assertIn("conflicting_rule_source", str(result.warnings))
        self.assertEqual(result.rule_results[0].source_name, "structured_league_rule")

    def test_equal_priority_conflict_is_unresolved(self):
        plan = make_plan(rr(RuleType.SALARY_CAP.value))
        result = evaluate(
            plan,
            rules=[
                rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225}, priority=1, source="rule_a"),
                rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 200}, priority=1, source="rule_b"),
            ],
            caps=[cap()],
        )
        self.assertEqual(result.overall_status, OverallStatus.UNVERIFIABLE.value)
        self.assertIn("Conflicting verified rule sources", str(result.unresolved_rules))

    def test_generic_fantasy_assumptions_are_never_used(self):
        result = evaluate(make_plan(rr(RuleType.TAXI_SQUAD_ELIGIBILITY.value)), players=[player(experience=0)])
        self.assertEqual(result.overall_status, OverallStatus.UNVERIFIABLE.value)
        self.assertIn("No verified canonical taxi eligibility rule", str(result.unresolved_rules))


class TradeTaxiRosterCapAndDraftRulesTest(unittest.TestCase):
    def test_trade_asset_ownership_and_pick_rules(self):
        ownership_ok = evaluate(with_planned_players(make_plan(rr(RuleType.ASSET_OWNERSHIP.value)), ["p1"]), teams=[team(players=["p1"])])
        ownership_bad = evaluate(with_planned_players(make_plan(rr(RuleType.ASSET_OWNERSHIP.value)), ["p1"]), teams=[team(players=["p-other"])])
        pick_ok = evaluate(make_plan(rr(RuleType.PICK_OWNERSHIP.value)), picks=[DraftPickEvidence("2026_1.03", 2026, 1, 3, "team-2", "team-1", "tradeable", True, ["draft_picks"])])
        pick_bad = evaluate(make_plan(rr(RuleType.PICK_OWNERSHIP.value)), picks=[DraftPickEvidence("2026_1.03", 2026, 1, 3, "team-2", "team-2", "tradeable", True, ["draft_picks"])])
        not_tradeable = evaluate(make_plan(rr(RuleType.PICK_TRADEABILITY.value)), picks=[DraftPickEvidence("2026_1.03", 2026, 1, 3, "team-2", "team-1", "locked", True, ["draft_picks"])])
        self.assertEqual(ownership_ok.overall_status, OverallStatus.LEGAL.value)
        self.assertEqual(ownership_bad.overall_status, OverallStatus.ILLEGAL.value)
        self.assertEqual(pick_ok.overall_status, OverallStatus.LEGAL.value)
        self.assertEqual(pick_bad.overall_status, OverallStatus.ILLEGAL.value)
        self.assertEqual(not_tradeable.overall_status, OverallStatus.ILLEGAL.value)

    def test_deadline_open_closed_and_equal_to_deadline(self):
        plan = make_plan(rr(RuleType.TRADE_DEADLINE.value))
        source = [rule_evidence(RuleType.TRADE_DEADLINE.value, {"deadline": "2026-10-31T12:00:00-06:00"})]
        before = evaluate(plan, rules=source, now=datetime(2026, 10, 31, 11, 59, tzinfo=ZoneInfo("America/Boise")))
        equal = evaluate(plan, rules=source, now=datetime(2026, 10, 31, 12, 0, tzinfo=ZoneInfo("America/Boise")))
        after = evaluate(plan, rules=source, now=datetime(2026, 10, 31, 12, 1, tzinfo=ZoneInfo("America/Boise")))
        self.assertEqual(before.overall_status, OverallStatus.LEGAL.value)
        self.assertEqual(equal.overall_status, OverallStatus.LEGAL.value)
        self.assertEqual(after.overall_status, OverallStatus.ILLEGAL.value)

    def test_taxi_verified_eligible_ineligible_and_missing_rookie_draft_evidence(self):
        eligible = evaluate(make_plan(rr(RuleType.TAXI_SQUAD_ELIGIBILITY.value)), rules=[rule_evidence(RuleType.TAXI_SQUAD_ELIGIBILITY.value, {"max_experience": 1})], players=[player(experience=1)])
        ineligible = evaluate(make_plan(rr(RuleType.TAXI_SQUAD_ELIGIBILITY.value)), rules=[rule_evidence(RuleType.TAXI_SQUAD_ELIGIBILITY.value, {"max_experience": 1})], players=[player(experience=2)])
        missing_rookie_draft = evaluate(make_plan(rr(RuleType.TAXI_SQUAD_ELIGIBILITY.value)), rules=[rule_evidence(RuleType.TAXI_SQUAD_ELIGIBILITY.value, {"rookie_draft_required": True})], players=[player(experience=0)])
        selected = evaluate(make_plan(rr(RuleType.TAXI_SQUAD_ELIGIBILITY.value)), rules=[rule_evidence(RuleType.TAXI_SQUAD_ELIGIBILITY.value, {"rookie_draft_required": True, "max_experience": 1})], players=[player(experience=0, profile={"rookie_draft_selected": True})])
        self.assertEqual(eligible.overall_status, OverallStatus.LEGAL.value)
        self.assertEqual(ineligible.overall_status, OverallStatus.ILLEGAL.value)
        self.assertEqual(missing_rookie_draft.overall_status, OverallStatus.UNVERIFIABLE.value)
        self.assertEqual(selected.overall_status, OverallStatus.LEGAL.value)

    def test_roster_current_and_hypothetical(self):
        current_ok = evaluate(make_plan(rr(RuleType.ROSTER_SIZE.value)), rules=[rule_evidence(RuleType.ROSTER_SIZE.value, {"roster_limit": 3})], teams=[team(players=["p1", "p2"])])
        current_bad = evaluate(make_plan(rr(RuleType.ROSTER_SIZE.value)), rules=[rule_evidence(RuleType.ROSTER_SIZE.value, {"roster_limit": 1})], teams=[team(players=["p1", "p2"])])
        hypothetical = evaluate(make_plan(rr(RuleType.ROSTER_SIZE.value), plan_type=PlanType.ROSTER_MOVE.value), rules=[rule_evidence(RuleType.ROSTER_SIZE.value, {"roster_limit": 3})], teams=[team(players=["p1", "p2"])])
        missing = evaluate(make_plan(rr(RuleType.ROSTER_SIZE.value)), rules=[rule_evidence(RuleType.ROSTER_SIZE.value, {"roster_limit": 3})])
        self.assertEqual(current_ok.overall_status, OverallStatus.LEGAL.value)
        self.assertEqual(current_bad.overall_status, OverallStatus.ILLEGAL.value)
        self.assertEqual(hypothetical.overall_status, OverallStatus.CONDITIONALLY_LEGAL.value)
        self.assertEqual(missing.overall_status, OverallStatus.UNVERIFIABLE.value)

    def test_salary_cap_current_and_hypothetical(self):
        compliant = evaluate(make_plan(rr(RuleType.SALARY_CAP.value)), rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap()])
        over = evaluate(make_plan(rr(RuleType.SALARY_CAP.value)), rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap(cap_space=-1)])
        trade = evaluate(make_plan(rr(RuleType.SALARY_CAP.value), plan_type=PlanType.TRADE_EVALUATION.value), rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap()])
        missing = evaluate(make_plan(rr(RuleType.SALARY_CAP.value)))
        self.assertEqual(compliant.overall_status, OverallStatus.LEGAL.value)
        self.assertEqual(over.overall_status, OverallStatus.ILLEGAL.value)
        self.assertEqual(trade.overall_status, OverallStatus.CONDITIONALLY_LEGAL.value)
        self.assertIn("post_transaction_cap_total", [c.calculation_type for c in trade.required_calculations])
        self.assertEqual(missing.overall_status, OverallStatus.UNVERIFIABLE.value)


class ContractDraftFreeAgentLineupIRTest(unittest.TestCase):
    def test_contract_rules_defer_action_calculations(self):
        active = ContractEvidence("p1", "team-1", 2026, 10, 2, "active", {}, {}, ["contracts"])
        inactive = ContractEvidence("p1", "team-1", 2026, 10, 0, "expired", {}, {}, ["contracts"])
        missing = evaluate(make_plan(rr(RuleType.CONTRACT_ELIGIBILITY.value)))
        eligible = evaluate(make_plan(rr(RuleType.EXTENSION_ELIGIBILITY.value)), rules=[rule_evidence(RuleType.EXTENSION_ELIGIBILITY.value, {"allowed": True})], contracts=[active])
        bad = evaluate(make_plan(rr(RuleType.CONTRACT_ELIGIBILITY.value)), contracts=[inactive])
        self.assertEqual(missing.overall_status, OverallStatus.UNVERIFIABLE.value)
        self.assertEqual(eligible.overall_status, OverallStatus.CONDITIONALLY_LEGAL.value)
        self.assertIn("extension_schedule", [c.calculation_type for c in eligible.required_calculations])
        self.assertEqual(bad.overall_status, OverallStatus.ILLEGAL.value)

    def test_ir_rules_require_status_and_do_not_add_external_provider(self):
        eligible = evaluate(make_plan(rr(RuleType.INJURED_RESERVE_ELIGIBILITY.value)), rules=[rule_evidence(RuleType.INJURED_RESERVE_ELIGIBILITY.value, {"eligible_statuses": ["IR"]})], players=[player(status="IR")])
        ineligible = evaluate(make_plan(rr(RuleType.INJURED_RESERVE_ELIGIBILITY.value)), rules=[rule_evidence(RuleType.INJURED_RESERVE_ELIGIBILITY.value, {"eligible_statuses": ["IR"]})], players=[player(status="healthy")])
        missing_status = evaluate(make_plan(rr(RuleType.INJURED_RESERVE_ELIGIBILITY.value)), rules=[rule_evidence(RuleType.INJURED_RESERVE_ELIGIBILITY.value, {"eligible_statuses": ["IR"]})], players=[player(status=None)])
        self.assertEqual(eligible.overall_status, OverallStatus.LEGAL.value)
        self.assertIn("ir_cap_treatment", [c.calculation_type for c in eligible.required_calculations])
        self.assertEqual(ineligible.overall_status, OverallStatus.ILLEGAL.value)
        self.assertEqual(missing_status.overall_status, OverallStatus.UNVERIFIABLE.value)

    def test_free_agent_and_waiver_rules(self):
        available = FreeAgentEvidence("p-fa", "Free Player", "RB", True, "free_agents", {}, {}, [])
        verified = evaluate(make_plan(rr(RuleType.FREE_AGENT_AVAILABILITY.value)), free_agents=[available])
        missing = evaluate(make_plan(rr(RuleType.FREE_AGENT_AVAILABILITY.value)))
        self.assertEqual(verified.overall_status, OverallStatus.CONDITIONALLY_LEGAL.value)
        self.assertIn("post_acquisition_roster_room", [c.calculation_type for c in verified.required_calculations])
        self.assertEqual(missing.overall_status, OverallStatus.UNVERIFIABLE.value)

    def test_lineup_rules_do_not_recommend_starters(self):
        unlocked = LineupEvidence("team-1", 2026, 1, ["p1"], ["p2"], {"flex": ["RB", "WR"]}, {}, {}, ["lineup"])
        locked = LineupEvidence("team-1", 2026, 1, ["p1"], ["p2"], {"locked": True}, {}, {}, ["lineup"])
        missing_week = LineupEvidence("team-1", 2026, None, ["p1"], ["p2"], {"flex": ["RB"]}, {}, {}, ["lineup"])
        result = evaluate(make_plan(rr(RuleType.LINEUP_ELIGIBILITY.value)), lineups=[unlocked])
        locked_result = evaluate(make_plan(rr(RuleType.LINEUP_ELIGIBILITY.value)), lineups=[locked])
        missing = evaluate(make_plan(rr(RuleType.LINEUP_ELIGIBILITY.value)), lineups=[missing_week])
        self.assertEqual(result.overall_status, OverallStatus.CONDITIONALLY_LEGAL.value)
        self.assertNotIn("recommend", str(result).lower())
        self.assertEqual(locked_result.overall_status, OverallStatus.ILLEGAL.value)
        self.assertEqual(missing.overall_status, OverallStatus.UNVERIFIABLE.value)


class RulesSerializationOpenAICompatibilityTest(unittest.TestCase):
    def test_rules_packet_is_compact_and_labeled(self):
        result = evaluate(make_plan(rr(RuleType.SALARY_CAP.value)), rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap(cap_space=-1)])
        packet = build_rules_packet(result)
        self.assertEqual(packet["overall_status"], OverallStatus.ILLEGAL.value)
        self.assertIn("violations", packet)
        text = str(packet).lower()
        self.assertNotIn("traceback", text)
        self.assertNotIn("raw_row", text)
        self.assertNotIn("service_key", text)

    def test_openai_service_accepts_rules_omitted_or_supplied(self):
        service = importlib.import_module("gm_assistant.openai_service")
        rules = evaluate(make_plan(rr(RuleType.SALARY_CAP.value)), rules=[rule_evidence(RuleType.SALARY_CAP.value, {"salary_cap": 225})], caps=[cap(cap_space=-1)])
        without_rules = service._build_initial_messages("Question?", [], None, None, None, None, None, None)
        with_rules = service._build_initial_messages("Question?", [], None, None, None, None, None, rules)
        self.assertEqual(without_rules[-1]["content"], "Question?")
        self.assertIn("Structured rules evaluation", with_rules[0]["content"])
        self.assertIn("illegal", with_rules[0]["content"])
        self.assertEqual(with_rules[-1]["content"], "Question?")

    def test_page_pipeline_can_evaluate_rules_after_evidence(self):
        from tests.test_assistant_evidence import packet_for as evidence_packet_for, req as evidence_req
        from tests.test_assistant_planning import FakeClient, make_context as planner_context, make_objective as planner_objective, make_state as planner_state
        from gm_assistant.evidence import ProviderResult, build_evidence_packet
        from gm_assistant.interpretation import interpret_question
        from gm_assistant.planning import build_decision_plan

        class Provider:
            def get_rule_sources(self, _context, _request):
                return ProviderResult.success([{"rule_type": "salary_cap", "season": 2026, "structured_value": {"salary_cap": 225}, "source_priority": 1, "verified": True}], "league_rules")
            def get_cap_summary(self, _context, _request):
                return ProviderResult.success([{"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "cap_space": 12, "salary_cap": 225}], "v_team_caps")

        context = planner_context()
        state = planner_state()
        interpreted = interpret_question("Is this move legal under the cap?", context, state, sb=FakeClient())
        objective = planner_objective(context, state, interpreted)
        plan = build_decision_plan(context=context, conversation_state=state, interpreted_question=interpreted, owner_objective=objective)
        packet = build_evidence_packet(context=context, conversation_state=state, interpreted_question=interpreted, owner_objective=objective, decision_plan=plan, retrieval_provider=Provider())
        result = evaluate_rules(context=context, interpreted_question=interpreted, owner_objective=objective, decision_plan=plan, evidence_packet=packet, now=datetime(2026, 7, 20, tzinfo=ZoneInfo("America/Boise")))
        self.assertEqual(result.overall_status, OverallStatus.UNVERIFIABLE.value)
        self.assertIn("Cap evidence is missing", str(result.unresolved_rules))


if __name__ == "__main__":
    unittest.main()
