from __future__ import annotations

import importlib
import sys
import types
import unittest
from dataclasses import replace

from gm_assistant.conversation_state import ConversationState
from gm_assistant.evidence import (
    EvidenceExecutionStatus,
    ProviderResult,
    RetrievalStatus,
    build_evidence_packet,
    build_evidence_packet_payload,
)
from gm_assistant.interpretation import Intent, InterpretedQuestion
from gm_assistant.objective import Goal, OwnerObjective
from gm_assistant.planning import (
    DecisionPlan,
    PlanType,
    RetrievalRequest,
    build_decision_plan,
)
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE


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
        "team_name": "Team One",
        "owner_name": "Owner One",
    }
    data.update(overrides)
    return AssistantRequestContext(**data)


def make_state(**overrides):
    data = {
        "conversation_id": "conversation-1",
        "user_id": "user-1",
        "league_id": "league-1",
        "league_team_id": "team-1",
    }
    data.update(overrides)
    return ConversationState(**data)


def make_interpreted(intent=Intent.PLAYER_EVALUATION.value):
    return InterpretedQuestion(
        raw_question="What do you think about Garrett Wilson?",
        primary_intent=intent,
        player_refs=[],
        confidence="high",
    )


def make_objective(goal=Goal.EVALUATE_ASSET.value):
    return OwnerObjective(
        request_goal=goal,
        active_strategic_goal="contend_this_season",
        primary_goal=goal,
        confidence="high",
    )


def req(retrieval_type, *, scope="league", required=True, team_ids=None, player_ids=None, pick_ids=None, seasons=None, filters=None):
    filters = {"league_id": "league-1", **(filters or {})}
    return RetrievalRequest(
        retrieval_type=retrieval_type,
        scope=scope,
        team_ids=list(team_ids or []),
        player_ids=list(player_ids or []),
        pick_ids=list(pick_ids or []),
        seasons=list(seasons or [2026]),
        filters=filters,
        required=required,
        reason=f"Need {retrieval_type}.",
    )


def make_plan(*requests, plan_type=PlanType.PLAYER_EVALUATION.value, ready=True, blockers=None):
    return DecisionPlan(
        plan_type=plan_type,
        request_goal=Goal.EVALUATE_ASSET.value,
        active_strategic_goal="contend_this_season",
        decision_engine="player_evaluation_engine",
        response_mode="structured_evaluation",
        retrieval_requests=list(requests),
        blockers=list(blockers or []),
        ready_for_execution=ready,
        confidence="high",
    )


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.results = {
            "get_team_roster": ProviderResult.success([
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Garrett Wilson", "sleeper_id": "p1", "position": "WR"}
            ], "team_roster_state"),
            "get_team_brain": ProviderResult.success([
                {"league_id": "league-1", "league_team_id": "team-1", "team_name": "Team One", "team_direction": "CONTEND_NOW", "position_needs": ["RB"]}
            ], "team_brain"),
            "get_team_roster_summary": ProviderResult.success([
                {"league_id": "league-1", "league_team_id": "team-1", "sleeper_id": "p1"},
                {"league_id": "league-1", "league_team_id": "team-2", "sleeper_id": "p2"},
            ], "team_roster_state"),
            "get_league_brain": ProviderResult.success([
                {"league_id": "league-1", "season": 2026, "league_size": 10, "league_status": "active"}
            ], "league_brain"),
            "get_team_brain_rankings": ProviderResult.success([
                {"league_id": "league-1", "league_team_id": "team-1", "team_name": "Team One"},
                {"league_id": "league-1", "league_team_id": "team-2", "team_name": "Team Two"},
            ], "team_brain"),
            "get_cap_summary": ProviderResult.success([
                {"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "cap_space": 12, "total_salary": 213, "dead_cap": 1}
            ], "v_team_caps"),
            "get_draft_picks": ProviderResult.success([
                {"league_id": "league-1", "canonical_pick_id": "2026_1.03", "season": 2026, "round": 1, "slot": 3, "current_owner_team_id": "team-1", "original_team_id": "team-2"}
            ], "draft_picks"),
            "get_transactions": ProviderResult.success([], "transactions_enriched"),
            "get_player_profiles": ProviderResult.success([
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Garrett Wilson", "sleeper_id": "p1", "position": "WR", "age": "25.2", "strategic_label": "CORE", "league_value_tier": "TOP_STARTER"},
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Garrett Wilson Duplicate", "sleeper_id": "p1", "position": "WR"},
            ], "player_strategic_profiles"),
            "get_player_contracts": ProviderResult.success([
                {"league_id": "league-1", "league_team_id": "team-1", "player_name": "Garrett Wilson", "sleeper_player_id": "p1", "salary": "24", "contract_years_left": 2}
            ], "contracts"),
            "get_league_settings": ProviderResult.success([
                {"league_id": "league-1", "season": 2026, "league_size": 10, "scoring_summary": {"ppr": 1}}
            ], "league_settings"),
            "get_rule_sources": ProviderResult.success([
                {"league_id": "league-1", "rule_type": "taxi_eligibility", "season": 2026, "structured_value": {"rookies": True}, "source_priority": 1, "verified": True}
            ], "league_rules"),
            "get_lineup_sources": ProviderResult.success([
                {"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "week": 1, "starter_player_ids": ["p1"], "bench_player_ids": ["p2"], "eligible_positions": {"flex": ["RB", "WR", "TE"]}}
            ], "lineup_sources"),
            "get_free_agent_sources": ProviderResult.success([
                {"league_id": "league-1", "sleeper_id": "p-fa", "player_name": "Free Player", "position": "RB", "availability_verified": True, "expected_cost": {"salary": 1}}
            ], "free_agents"),
        }

    def _call(self, method, _context, _request):
        self.calls.append(method)
        result = self.results[method]
        if isinstance(result, BaseException):
            raise result
        return result

    def get_team_roster(self, context, request): return self._call("get_team_roster", context, request)
    def get_team_brain(self, context, request): return self._call("get_team_brain", context, request)
    def get_team_roster_summary(self, context, request): return self._call("get_team_roster_summary", context, request)
    def get_league_brain(self, context, request): return self._call("get_league_brain", context, request)
    def get_team_brain_rankings(self, context, request): return self._call("get_team_brain_rankings", context, request)
    def get_cap_summary(self, context, request): return self._call("get_cap_summary", context, request)
    def get_draft_picks(self, context, request): return self._call("get_draft_picks", context, request)
    def get_transactions(self, context, request): return self._call("get_transactions", context, request)
    def get_player_profiles(self, context, request): return self._call("get_player_profiles", context, request)
    def get_player_contracts(self, context, request): return self._call("get_player_contracts", context, request)
    def get_league_settings(self, context, request): return self._call("get_league_settings", context, request)
    def get_rule_sources(self, context, request): return self._call("get_rule_sources", context, request)
    def get_lineup_sources(self, context, request): return self._call("get_lineup_sources", context, request)
    def get_free_agent_sources(self, context, request): return self._call("get_free_agent_sources", context, request)


def packet_for(plan, *, context=None, state=None, provider=None, interpreted=None, objective=None):
    context = context or make_context()
    return build_evidence_packet(
        context=context,
        conversation_state=state if state is not None else make_state(),
        interpreted_question=interpreted or make_interpreted(),
        owner_objective=objective or make_objective(),
        decision_plan=plan,
        retrieval_provider=provider or FakeProvider(),
    )


class EvidenceContextScopeTest(unittest.TestCase):
    def test_evidence_context_matches_request_context(self):
        packet = packet_for(make_plan(req("cap_summary", scope="team", team_ids=["team-1"])))
        self.assertEqual(packet.request_context_ref.user_id, "user-1")
        self.assertEqual(packet.request_context_ref.league_id, "league-1")
        self.assertEqual(packet.request_context_ref.league_team_id, "team-1")
        self.assertEqual(packet.request_context_ref.conversation_id, "conversation-1")

    def test_mismatched_user_league_team_or_conversation_is_rejected(self):
        plan = make_plan(req("cap_summary", scope="team", team_ids=["team-1"]))
        cases = [
            make_state(user_id="user-x"),
            make_state(league_id="league-x"),
            make_state(league_team_id="team-x"),
            make_state(conversation_id="conversation-x"),
        ]
        for state in cases:
            with self.subTest(state=state):
                packet = packet_for(plan, state=state)
                self.assertEqual(packet.execution_status, EvidenceExecutionStatus.BLOCKED.value)
                self.assertFalse(packet.required_evidence_complete)
                self.assertFalse(FakeProvider().calls)

    def test_cross_league_or_cross_team_plan_is_blocked_before_provider_call(self):
        provider = FakeProvider()
        plan = make_plan(req("team_roster", scope="team", team_ids=["team-x"]))
        packet = packet_for(plan, provider=provider)
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.BLOCKED.value)
        self.assertEqual(provider.calls, [])
        self.assertTrue(any(item.requirement_type == "scope_mismatch" for item in packet.unresolved_requirements))

    def test_commissioner_scope_does_not_bypass_cross_team_scope(self):
        context = make_context(role="commissioner", permission_scopes=(TEAM_ADVICE, LEAGUE_PUBLIC_READ, "league_admin"))
        provider = FakeProvider()
        packet = packet_for(make_plan(req("team_roster", scope="team", team_ids=["team-2"])), context=context, provider=provider)
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.BLOCKED.value)
        self.assertEqual(provider.calls, [])

    def test_plan_league_filter_mismatch_is_rejected(self):
        provider = FakeProvider()
        packet = packet_for(make_plan(req("cap_summary", filters={"league_id": "league-x"})), provider=provider)
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.BLOCKED.value)
        self.assertEqual(provider.calls, [])


class EvidencePlanExecutionTest(unittest.TestCase):
    def test_only_planned_retrievals_execute_and_duplicates_dedupe(self):
        provider = FakeProvider()
        plan = make_plan(
            req("cap_summary", scope="team", team_ids=["team-1"]),
            req("cap_summary", scope="team", team_ids=["team-1"]),
            req("player_contract", player_ids=["p1"]),
        )
        packet = packet_for(plan, provider=provider)
        self.assertEqual(provider.calls, ["get_cap_summary", "get_player_contracts"])
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.COMPLETE.value)

    def test_blocked_plan_and_unsupported_plan_do_not_execute(self):
        provider = FakeProvider()
        blocked = make_plan(req("cap_summary"), ready=False, blockers=[types.SimpleNamespace(blocker_type="unresolved_player", explanation="Need player.")])
        packet = packet_for(blocked, provider=provider)
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.BLOCKED.value)
        self.assertEqual(provider.calls, [])

        unsupported = make_plan(plan_type=PlanType.UNSUPPORTED.value)
        unsupported_packet = packet_for(unsupported, provider=provider)
        self.assertEqual(unsupported_packet.execution_status, EvidenceExecutionStatus.BLOCKED.value)
        self.assertEqual(provider.calls, [])

    def test_factual_lookup_executes_minimal_retrieval(self):
        provider = FakeProvider()
        plan = make_plan(req("player_contract", player_ids=["p1"]), plan_type=PlanType.FACTUAL_LOOKUP.value)
        packet = packet_for(plan, provider=provider)
        self.assertEqual(provider.calls, ["get_player_contracts"])
        self.assertEqual(len(packet.contract_evidence), 1)

    def test_provider_exception_becomes_structured_failure(self):
        provider = FakeProvider()
        provider.results["get_cap_summary"] = RuntimeError("boom")
        packet = packet_for(make_plan(req("cap_summary", scope="team", team_ids=["team-1"])), provider=provider)
        self.assertEqual(packet.retrieval_results[0].status, RetrievalStatus.FAILED.value)
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.FAILED.value)
        self.assertNotIn("boom", str(build_evidence_packet_payload(packet)))


class EvidenceResultCompletenessTest(unittest.TestCase):
    def test_empty_is_distinct_from_failure_and_can_satisfy_transactions(self):
        provider = FakeProvider()
        packet = packet_for(make_plan(req("recent_transactions")), provider=provider)
        self.assertEqual(packet.retrieval_results[0].status, RetrievalStatus.EMPTY.value)
        self.assertTrue(packet.required_evidence_complete)
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.COMPLETE.value)

    def test_required_unavailable_blocks_but_optional_unavailable_reduces(self):
        provider = FakeProvider()
        provider.results["get_rule_sources"] = ProviderResult.unavailable("league_rules", "No trusted structured rule source is available.")
        required = packet_for(make_plan(req("league_rules")), provider=provider)
        self.assertFalse(required.required_evidence_complete)
        self.assertEqual(required.execution_status, EvidenceExecutionStatus.BLOCKED.value)

        provider = FakeProvider()
        provider.results["get_lineup_sources"] = ProviderResult.unavailable("weekly_projection_summary", "Projection source is unavailable.")
        optional = packet_for(make_plan(req("weekly_projection_summary", required=False)), provider=provider)
        self.assertTrue(optional.required_evidence_complete)
        self.assertTrue(optional.reduced_mode)
        self.assertEqual(optional.execution_status, EvidenceExecutionStatus.REDUCED.value)

    def test_warning_only_packet_status(self):
        provider = FakeProvider()
        provider.results["get_cap_summary"] = ProviderResult(RetrievalStatus.SUCCESS.value, [{"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "salary_cap": 100, "active_salary": 50, "dead_cap": 10, "available_cap": 99}], "v_team_caps")
        packet = packet_for(make_plan(req("cap_summary", scope="team", team_ids=["team-1"])), provider=provider)
        self.assertTrue(packet.required_evidence_complete)
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.COMPLETE_WITH_WARNINGS.value)
        self.assertIn("cap_total_fields_are_inconsistent", str(packet.warnings))


class EvidenceNormalizationTest(unittest.TestCase):
    def test_player_contract_cap_team_draft_rule_lineup_and_free_agent_models(self):
        plan = make_plan(
            req("player_profile", player_ids=["p1"]),
            req("player_contract", player_ids=["p1"]),
            req("cap_summary", scope="team", team_ids=["team-1"]),
            req("team_roster", scope="team", team_ids=["team-1"]),
            req("team_brain", scope="team", team_ids=["team-1"]),
            req("draft_pick", pick_ids=["2026_1.03"]),
            req("league_rules"),
            req("league_brain"),
            req("eligible_roster_players", scope="team", team_ids=["team-1"]),
            req("free_agent_pool"),
        )
        packet = packet_for(plan)
        self.assertEqual(len(packet.player_evidence), 1)
        self.assertEqual(packet.player_evidence[0].player_id, "p1")
        self.assertEqual(packet.contract_evidence[0].salary, 24)
        self.assertEqual(packet.cap_evidence[0].league_team_id, "team-1")
        self.assertEqual(packet.draft_pick_evidence[0].current_owner_team_id, "team-1")
        self.assertTrue(packet.rules_evidence[0].verified)
        self.assertEqual(packet.lineup_evidence[0].starter_player_ids, ["p1"])
        self.assertTrue(packet.free_agent_evidence[0].availability_verified)
        self.assertFalse(any("recommendation" in str(item).lower() for item in packet.player_evidence))

    def test_malformed_player_and_contract_rows_are_warned_not_fabricated(self):
        provider = FakeProvider()
        provider.results["get_player_profiles"] = ProviderResult.success([{"player_name": "No ID"}], "player_strategic_profiles")
        provider.results["get_player_contracts"] = ProviderResult.success([{"player_name": "No ID", "salary": "bad"}], "contracts")
        packet = packet_for(make_plan(req("player_profile"), req("player_contract")), provider=provider)
        self.assertEqual(packet.player_evidence, [])
        self.assertEqual(packet.contract_evidence, [])
        self.assertIn("Malformed player evidence skipped", str(packet.warnings))
        self.assertIn("Malformed contract evidence skipped", str(packet.warnings))

    def test_free_agent_source_must_verify_availability(self):
        provider = FakeProvider()
        provider.results["get_free_agent_sources"] = ProviderResult.success([
            {"league_id": "league-1", "sleeper_id": "p-rostered", "player_name": "Rostered Player", "position": "RB", "availability_verified": False}
        ], "free_agents")
        packet = packet_for(make_plan(req("free_agent_pool")), provider=provider)
        self.assertEqual(packet.free_agent_evidence, [])


class EvidenceSerializationOpenAICompatibilityTest(unittest.TestCase):
    def test_payload_is_compact_and_excludes_raw_sensitive_provider_details(self):
        packet = packet_for(make_plan(req("player_profile", player_ids=["p1"])))
        payload = build_evidence_packet_payload(packet)
        self.assertIn("player_evidence", payload)
        self.assertIn("retrieval_results", payload)
        text = str(payload).lower()
        self.assertNotIn("raw_row", text)
        self.assertNotIn("traceback", text)
        self.assertNotIn("service_key", text)
        self.assertNotIn("personal memory", text)

    def test_openai_service_accepts_evidence_omitted_or_supplied(self):
        service = importlib.import_module("gm_assistant.openai_service")
        packet = packet_for(make_plan(req("cap_summary", scope="team", team_ids=["team-1"])))
        without_packet = service._build_initial_messages("Question?", [], None, None, None, None, None)
        with_packet = service._build_initial_messages("Question?", [], None, None, None, None, packet)
        self.assertEqual(without_packet[-1]["content"], "Question?")
        self.assertIn("Structured evidence packet", with_packet[0]["content"])
        self.assertIn("Use only verified facts", with_packet[0]["content"])
        self.assertEqual(with_packet[-1]["content"], "Question?")

    def test_page_and_pipeline_build_a_packet_from_stage_five_plan(self):
        from tests.test_assistant_planning import FakeClient, make_context as planner_context, make_objective as planner_objective, make_state as planner_state
        from gm_assistant.interpretation import interpret_question

        context = planner_context()
        state = planner_state()
        interpreted = interpret_question("How much cap space do I have?", context, state, sb=FakeClient())
        objective = planner_objective(context, state, interpreted)
        plan = build_decision_plan(
            context=context,
            conversation_state=state,
            interpreted_question=interpreted,
            owner_objective=objective,
        )
        provider = FakeProvider()
        provider.results["get_cap_summary"] = ProviderResult.success([
            {"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "cap_space": 12}
        ], "v_team_caps")
        packet = build_evidence_packet(
            context=context,
            conversation_state=state,
            interpreted_question=interpreted,
            owner_objective=objective,
            decision_plan=plan,
            retrieval_provider=provider,
        )
        self.assertEqual(provider.calls, ["get_cap_summary"])
        self.assertEqual(packet.execution_status, EvidenceExecutionStatus.COMPLETE.value)
        self.assertEqual(packet.cap_evidence[0].available_cap, 12)


if __name__ == "__main__":
    unittest.main()
