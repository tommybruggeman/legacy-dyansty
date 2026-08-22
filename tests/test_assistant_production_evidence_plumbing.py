from __future__ import annotations

import sys
import types
import unittest

from gm_assistant.assistant_pipeline import run_assistant_pipeline
from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE


auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.limit_value = None

    def select(self, _cols="*"):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        self.client.selects.append((self.table_name, list(self.filters)))
        if self.table_name in self.client.fail_tables:
            raise RuntimeError(f"{self.table_name} unavailable")
        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            rows = [row for row in rows if str(row.get(key)) == str(value)]
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return Result(rows)


class FakeTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def select(self, cols="*"):
        return FakeQuery(self.client, self.table_name).select(cols)


class FakeClient:
    gm_contract_read_mode="legacy"
    def __init__(self):
        self.fail_tables = {"v_team_caps"}
        self.selects = []
        self.rows = {
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Condor Dynasty", "owner_name": "Owner One"},
                {"id": "team-2", "league_id": "league-1", "team_name": "Rival Team", "owner_name": "Owner Two"},
                {"id": "team-x", "league_id": "league-2", "team_name": "Other League", "owner_name": "Other Owner"},
            ],
            "leagues": [
                {"id": "league-1", "name": "Legacy League"},
                {"id": "league-2", "name": "Other League"},
            ],
            "team_brain": [
                {"league_id": "league-1", "league_team_id": "team-1", "team_name": "Condor Dynasty", "owner_name": "Owner One"},
            ],
            "team_roster_state": [],
            "player_strategic_profiles": [
                {"league_id": "league-1", "league_team_id": "team-1", "sleeper_id": "4984", "player_name": "Josh Allen", "position": "QB"},
                {"league_id": "league-1", "league_team_id": "team-2", "sleeper_id": "9999", "player_name": "Other Josh Allen", "position": "QB"},
            ],
            "league_relative_player_values": [],
            "contracts": [
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Josh Allen", "player_position": "QB", "sleeper_player_id": "4984", "salary": 48, "contract_years_left": 3, "is_rookie": False},
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Breece Hall", "player_position": "RB", "sleeper_player_id": "breece", "salary": 18, "contract_years_left": 1, "is_rookie": False},
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "MarShawn Lloyd", "player_position": "RB", "sleeper_player_id": "lloyd", "salary": 4, "contract_years_left": 1, "is_rookie": True},
                {"league_id": "league-1", "owner_name": "Owner Two", "player_name": "Cross Team", "player_position": "WR", "sleeper_player_id": "cross-team", "salary": 10, "contract_years_left": 1, "is_rookie": False},
                {"league_id": "league-2", "owner_name": "Other Owner", "player_name": "Cross League", "player_position": "WR", "sleeper_player_id": "cross-league", "salary": 99, "contract_years_left": 1, "is_rookie": False},
            ],
            "league_rules": [
                {"league_id": "league-1", "salary_cap": 100, "taxi_limit": 1, "roster_limit": 22, "drop_dead_cap_multiplier": 0.5, "rookie_scale_enabled": True},
            ],
            "cap_adjustments": [
                {"league_id": "league-1", "owner_name": "Owner One", "season": 2026, "adjustment_type": "dropped_player_charge", "amount": 2},
                {"league_id": "league-1", "owner_name": "Owner One", "season": 2026, "adjustment_type": "trade_carryover", "amount": 3},
                {"league_id": "league-1", "owner_name": "Owner Two", "season": 2026, "adjustment_type": "manual_adjustment", "amount": 50},
            ],
            "draft_picks": [],
            "transactions_enriched": [],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


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
        "team_name": "Condor Dynasty",
        "owner_name": "Owner One",
    }
    data.update(overrides)
    return AssistantRequestContext(**data)


def run_question(question: str, *, client: FakeClient | None = None):
    client = client or FakeClient()
    return run_assistant_pipeline(
        context=make_context(),
        question=question,
        retrieval_provider=SupabaseEvidenceRetrievalProvider(client),
        interpreter_sb=client,
        owner_preferences={},
        team_context={},
    )


class ProductionEvidencePlumbingTest(unittest.TestCase):
    def test_team_identity_does_not_require_roster_rows(self):
        client = FakeClient()
        client.rows["contracts"] = []
        result = run_question("What team am I managing?", client=client)

        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertIn("Condor Dynasty", result.displayed_answer)
        self.assertIn("Legacy League", result.displayed_answer)
        self.assertNotIn("roster_count", result.displayed_answer)

    def test_team_identity_variants_are_direct_facts_without_calculations_or_recommendations(self):
        variants = [
            "What team am I managing?",
            "What team do I manage?",
            "Which team is mine?",
            "Who is my team?",
            "What is my team name?",
            "Which franchise do I control?",
            "Tell me my team.",
            "what team do I manage",
        ]
        for question in variants:
            with self.subTest(question=question):
                result = run_question(question)
                self.assertEqual(result.interpreted_question.primary_intent, "data_lookup")
                self.assertEqual(result.decision_plan.retrieval_requests[0].retrieval_type, "current_user_context")
                self.assertEqual(result.decision_plan.calculation_requests, [])
                self.assertEqual(result.decision_output.decision_type, "factual_response")
                self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
                self.assertIsNone(result.answer_packet.recommendation)
                self.assertIn("Condor Dynasty", result.displayed_answer)
                self.assertIn("Legacy League", result.displayed_answer)
                self.assertNotIn("validated recommendation", result.displayed_answer.lower())
                self.assertNotIn("not applicable", result.displayed_answer.lower())
                self.assertNotIn("structured recommendation", result.displayed_answer.lower())

    def test_team_identity_uses_canonical_team_without_team_brain_or_roster(self):
        client = FakeClient()
        client.rows["team_brain"] = []
        client.rows["team_roster_state"] = []
        client.rows["contracts"] = []

        result = run_question("what team do I manage", client=client)

        self.assertEqual(result.evidence_packet.retrieval_results[0].source_name, "league_teams")
        self.assertIn("Condor Dynasty", result.displayed_answer)
        self.assertIn("Legacy League", result.displayed_answer)
        self.assertEqual(result.evidence_packet.team_evidence[0].league_team_id, "team-1")
        self.assertNotIn("Rival Team", result.displayed_answer)

    def test_roster_uses_contract_owner_key_after_canonical_team_validation(self):
        result = run_question("Who is on my roster?")

        self.assertEqual(result.evidence_packet.retrieval_results[0].source_name, "contracts")
        self.assertEqual(len(result.evidence_packet.player_evidence), 3)
        self.assertIn("Josh Allen", result.displayed_answer)
        self.assertNotIn("Cross Team", result.displayed_answer)
        self.assertNotIn("Cross League", result.displayed_answer)

    def test_roster_list_variants_are_direct_facts_without_draft_or_recommendation(self):
        for question in ("Who is on my team?", "Who is on my roster?", "Show me my roster.", "List my players.", "Which players do I have?", "Who do I own?", "who is on my team"):
            with self.subTest(question=question):
                result = run_question(question)
                self.assertEqual(result.interpreted_question.primary_intent, "data_lookup")
                self.assertEqual(result.decision_plan.plan_type, "factual_lookup_plan")
                self.assertEqual(result.decision_plan.response_mode, "direct_factual")
                self.assertEqual([request.retrieval_type for request in result.decision_plan.retrieval_requests], ["team_roster"])
                self.assertEqual(result.decision_plan.calculation_requests, [])
                self.assertEqual(result.evidence_packet.draft_pick_evidence, [])
                self.assertEqual(result.decision_output.decision_type, "factual_response")
                self.assertEqual(result.recommendation_validation.validation_status, "not_applicable")
                self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
                self.assertIsNone(result.answer_packet.recommendation)
                self.assertIn("You currently have 3 players on your roster:", result.displayed_answer)
                self.assertIn("- Josh Allen - QB", result.displayed_answer)
                self.assertNotIn("Draft-pick evidence is missing", result.displayed_answer)
                self.assertNotIn("validated recommendation", result.displayed_answer.lower())
                self.assertNotIn("selected decision engine", result.displayed_answer.lower())
                self.assertNotIn("roster_count", result.displayed_answer)

    def test_production_shaped_contract_roster_lists_22_without_draft_evidence(self):
        client = FakeClient()
        client.rows["contracts"] = [
            {"league_id": "league-1", "owner_name": "Owner One", "player_name": f"Player {idx}", "player_position": "WR", "sleeper_player_id": f"p{idx}", "salary": 1, "contract_years_left": 2, "is_rookie": False}
            for idx in range(1, 21)
        ] + [
            {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Taxi Player", "player_position": "RB", "sleeper_player_id": "taxi", "status": "taxi", "salary": 1, "contract_years_left": 3, "is_rookie": True},
            {"league_id": "league-1", "owner_name": "Owner One", "player_name": "IR Player", "player_position": "TE", "sleeper_player_id": "ir", "status": "ir", "salary": 1, "contract_years_left": 3, "is_rookie": False},
            {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Duplicate Player", "player_position": "QB", "sleeper_player_id": "p1", "salary": 1, "contract_years_left": 2, "is_rookie": False},
            {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Released Player", "player_position": "QB", "sleeper_player_id": "released", "status": "released", "salary": 1, "contract_years_left": 2, "is_rookie": False},
            {"league_id": "league-1", "owner_name": "Owner Two", "player_name": "Cross Team", "player_position": "QB", "sleeper_player_id": "cross-team", "salary": 1, "contract_years_left": 2, "is_rookie": False},
        ]

        result = run_question("Who is on my team", client=client)

        self.assertEqual(result.evidence_packet.retrieval_results[0].source_name, "contracts")
        self.assertEqual(len(result.evidence_packet.player_evidence), 22)
        self.assertEqual(result.evidence_packet.draft_pick_evidence, [])
        self.assertEqual(result.decision_plan.calculation_requests, [])
        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
        self.assertIn("You currently have 22 players on your roster:", result.displayed_answer)
        self.assertIn("- Player 1 - WR", result.displayed_answer)
        self.assertIn("- Taxi Player - RB - TAXI", result.displayed_answer)
        self.assertIn("- IR Player - TE - IR", result.displayed_answer)
        self.assertNotIn("Released Player", result.displayed_answer)
        self.assertNotIn("Duplicate Player", result.displayed_answer)
        self.assertNotIn("Cross Team", result.displayed_answer)
        self.assertNotIn("draft-pick evidence", result.displayed_answer.lower())

    def test_zero_player_roster_response_is_direct_fact(self):
        client = FakeClient()
        client.rows["contracts"] = []
        client.rows["player_strategic_profiles"] = []
        result = run_question("Who is on my team", client=client)

        self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
        self.assertIn("No verified active roster players were found for your team.", result.displayed_answer)
        self.assertNotIn("validated recommendation", result.displayed_answer.lower())

    def test_one_year_contract_list_is_factual_and_team_scoped(self):
        result = run_question("Which players have one year left on their contracts?")

        self.assertEqual(result.interpreted_question.primary_intent, "contract_question")
        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertEqual(result.decision_output.action, "not_applicable")
        self.assertIn("Breece Hall", result.displayed_answer)
        self.assertIn("MarShawn Lloyd", result.displayed_answer)
        self.assertNotIn("request terms", result.displayed_answer.lower())
        self.assertNotIn("Cross Team", result.displayed_answer)

    def test_cap_summary_uses_production_formula_without_view(self):
        result = run_question("How much cap space do I have?")

        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertEqual(result.calculation_packet.results[-1].value, 25.0)
        self.assertEqual(result.displayed_answer, "You have 25.0 cap dollars in cap space for 2026.")
        self.assertEqual(result.evidence_packet.cap_evidence[0].source_fields["adjustment_total"], 5.0)
        self.assertNotIn("available_cap", result.displayed_answer)
        self.assertNotIn("Why:", result.displayed_answer)

    def test_negative_cap_summary_uses_over_cap_wording(self):
        client = FakeClient()
        client.rows["contracts"].append(
            {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Expensive Player", "player_position": "WR", "sleeper_player_id": "expensive", "salary": 40, "contract_years_left": 1}
        )

        result = run_question("How much cap space do I have?", client=client)

        self.assertEqual(result.displayed_answer, "You are 15.0 cap dollars over the cap for 2026.")
        self.assertNotIn("negative", result.displayed_answer.lower())
        self.assertNotIn("available_cap", result.displayed_answer)

    def test_best_and_worst_contracts_include_player_names_and_no_internal_fields(self):
        result = run_question("What are my best and worst contracts?")

        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
        self.assertIn("Using verified contract-structure-only signals:", result.displayed_answer)
        self.assertIn("Best contract signals:", result.displayed_answer)
        self.assertIn("Biggest contract concerns:", result.displayed_answer)
        self.assertIn("MarShawn Lloyd - RB: 4.0 cap dollars, 1 year remaining", result.displayed_answer)
        self.assertIn("Josh Allen - QB: 48.0 cap dollars, 3 years remaining", result.displayed_answer)
        self.assertNotIn("Contract:", result.displayed_answer)
        self.assertNotIn("contract_terms", result.displayed_answer)
        self.assertNotIn("validated recommendation", result.displayed_answer.lower())

    def test_draft_second_overall_limited_when_pick_and_prospects_are_unavailable(self):
        result = run_question("Who should I draft with the second overall rookie draft pick?")

        self.assertEqual(result.interpreted_question.primary_intent, "draft_recommendation")
        self.assertEqual([(pick.round, pick.slot) for pick in result.interpreted_question.pick_refs], [(1, 2)])
        self.assertIn("1.02", [pick_id for request in result.decision_plan.retrieval_requests for pick_id in request.pick_ids])
        self.assertEqual(result.answer_packet.answer_mode, "limited_information")
        self.assertIn("I cannot verify pick 1.02", result.displayed_answer)
        self.assertIn("verified internal rookie prospect pool", result.displayed_answer)
        self.assertNotIn("validated recommendation", result.displayed_answer.lower())
        self.assertNotIn("Draft-pick evidence is missing", result.displayed_answer)
        self.assertNotIn("Why:", result.displayed_answer)

    def test_draft_pick_label_and_owner_name_bridge_support_copy_from_my_team_table(self):
        client = FakeClient()
        client.rows["draft_picks"] = [
            {"league_id": "league-1", "season": 2026, "round": 1, "original_pick_rank": 2, "pick_label": "1.02", "current_owner": "Owner One", "original_team": "Owner Two"},
        ]

        result = run_question("Who should I draft with the second overall rookie draft pick?", client=client)

        self.assertEqual(result.evidence_packet.draft_pick_evidence[0].current_owner_team_id, "team-1")
        self.assertEqual(result.evidence_packet.draft_pick_evidence[0].original_team_id, "team-2")
        self.assertIn("I can verify that you hold pick 1.02", result.displayed_answer)
        self.assertIn("verified internal rookie prospect pool", result.displayed_answer)
        self.assertNotIn("My recommendation", result.displayed_answer)

    def test_release_impact_is_fact_not_execution_or_recommendation(self):
        result = run_question("What happens to my cap if I release Josh Allen?")

        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertIn("Releasing Josh Allen", result.displayed_answer)
        self.assertIn("creates 48.0 of dead cap", result.displayed_answer)
        self.assertIn("leaves projected cap space at 25.0", result.displayed_answer)
        self.assertNotIn("My recommendation", result.displayed_answer)

    def test_taxi_eligibility_uses_rookie_marker_without_cap_calculation(self):
        result = run_question("Can I put Josh Allen on taxi?")

        self.assertEqual(result.rules_evaluation.overall_status, "illegal")
        self.assertEqual(result.decision_output.action, "reject")
        self.assertEqual(result.decision_plan.calculation_requests, [])
        self.assertIn("No.", result.displayed_answer)
        self.assertIn("rookie", result.displayed_answer.lower())


if __name__ == "__main__":
    unittest.main()
