from __future__ import annotations

import inspect
import unittest

from gm_assistant.assistant_pipeline import run_assistant_pipeline
from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE
from gm_assistant.scenario_simulator import (
    MovePlayerToIR,
    MovePlayerToTaxi,
    ReleasePlayer,
    ScenarioActionType,
    ScenarioSimulatorService,
    TradePickOut,
    TradePlayerOut,
    parse_scenario_actions,
)
from gm_assistant.scenario_simulator import service as simulator_source


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
            ],
            "team_roster_state": [],
            "contracts": [
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Garrett Wilson", "player_position": "WR", "sleeper_player_id": "gw", "salary": 20, "contract_years_left": 2, "is_rookie": False},
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Drake London", "player_position": "WR", "sleeper_player_id": "dl", "salary": 18, "contract_years_left": 2, "is_rookie": False},
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Rookie Runner", "player_position": "RB", "sleeper_player_id": "rr", "salary": 4, "contract_years_left": 3, "is_rookie": True},
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Injured Tight End", "player_position": "TE", "sleeper_player_id": "ir-player", "salary": 2, "contract_years_left": 1, "status": "injured", "is_rookie": False},
                {"league_id": "league-1", "owner_name": "Owner Two", "player_name": "Cross League Trap", "player_position": "QB", "sleeper_player_id": "trap", "salary": 99, "contract_years_left": 1},
            ],
            "league_rules": [{"league_id": "league-1", "salary_cap": 100, "taxi_limit": 2, "roster_limit": 22}],
            "cap_adjustments": [{"league_id": "league-1", "owner_name": "Owner One", "season": 2026, "adjustment_type": "dropped_player_charge", "amount": 1}],
            "draft_picks": [{"league_id": "league-1", "season": 2028, "round": 1, "current_owner": "Owner One", "original_team": "Owner One", "pick_label": "2028 1st"}],
            "rookie_draft_board": [],
            "player_prospect_context": [],
            "rookie_class_registry": [],
            "rookie_draft_results": [],
            "draft_selections": [],
            "team_brain": [],
            "player_strategic_profiles": [],
            "league_relative_player_values": [],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


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


class ScenarioSimulatorTest(unittest.TestCase):
    def test_parser_supports_approved_examples(self):
        cases = {
            "What happens if I cut Garrett Wilson?": ScenarioActionType.RELEASE_PLAYER.value,
            "What would my cap be if I traded Garrett Wilson?": ScenarioActionType.TRADE_PLAYER_OUT.value,
            "What would my roster look like if I traded Garrett Wilson for Drake London?": ScenarioActionType.TRADE_PLAYER_OUT.value,
            "What happens if I trade my 2028 first?": ScenarioActionType.TRADE_PICK_OUT.value,
            "What happens if I move Injured Tight End to IR?": ScenarioActionType.MOVE_PLAYER_TO_IR.value,
        }
        for question, action_type in cases.items():
            with self.subTest(question=question):
                self.assertEqual(parse_scenario_actions(question)[0].action_type, action_type)

    def test_ambiguous_move_does_not_guess(self):
        self.assertEqual(parse_scenario_actions("What if I move Garrett Wilson?"), [])

    def test_release_player_projects_roster_and_dead_cap_without_mutating_source(self):
        client = FakeClient()
        service = ScenarioSimulatorService(client)
        before_rows = list(client.rows["contracts"])

        result = service.simulate(context(), [ReleasePlayer(player_name="Garrett Wilson")])

        self.assertEqual(result.status, "success")
        self.assertEqual(result.roster_delta.before_count, 4)
        self.assertEqual(result.roster_delta.after_count, 3)
        self.assertEqual(result.cap_delta.dead_cap_delta, 20.0)
        self.assertEqual(result.cap_delta.active_salary_delta, -20.0)
        self.assertEqual(client.rows["contracts"], before_rows)
        self.assertIn("Garrett Wilson", result.summary())

    def test_missing_contract_keeps_roster_delta_but_marks_cap_partial(self):
        client = FakeClient()
        client.rows["contracts"] = [row for row in client.rows["contracts"] if row["player_name"] != "Garrett Wilson"]
        client.rows["team_roster_state"] = [{"league_id": "league-1", "team_id": "team-1", "player_name": "Garrett Wilson", "sleeper_id": "gw", "position": "WR"}]

        result = ScenarioSimulatorService(client).simulate(context(), [ReleasePlayer(player_name="Garrett Wilson")])

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.completeness, "partial")
        self.assertIn("cap impact incomplete", " ".join(result.warnings))

    def test_trade_player_for_player_is_atomic_with_incomplete_incoming_cap(self):
        result = ScenarioSimulatorService(FakeClient()).simulate(
            context(),
            parse_scenario_actions("What would my roster look like if I traded Garrett Wilson for Drake London?"),
        )

        self.assertEqual([player.player_name for player in result.roster_delta.removed], ["Garrett Wilson"])
        self.assertEqual([player.player_name for player in result.roster_delta.added], ["Drake London"])
        self.assertEqual(result.completeness, "partial")

    def test_pick_trade_removes_only_verified_owned_pick(self):
        result = ScenarioSimulatorService(FakeClient()).simulate(context(), [TradePickOut(season=2028, round=1)])

        self.assertEqual(len(result.draft_capital_delta.picks_removed), 1)
        self.assertEqual(result.draft_capital_delta.picks_removed[0].current_owner_team_id, "team-1")

    def test_unowned_pick_blocks_atomic_scenario(self):
        result = ScenarioSimulatorService(FakeClient()).simulate(context(), [TradePickOut(season=2029, round=1)])

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.applied_actions, [])
        self.assertIn("not verified as owned", result.conflicts[0])

    def test_taxi_and_ir_eligibility_are_validated(self):
        service = ScenarioSimulatorService(FakeClient())

        taxi = service.simulate(context(), [MovePlayerToTaxi(player_name="Rookie Runner")])
        ir = service.simulate(context(), [MovePlayerToIR(player_name="Injured Tight End")])
        blocked = service.simulate(context(), [MovePlayerToTaxi(player_name="Garrett Wilson")])

        self.assertEqual(taxi.designation_delta.moved_to_taxi[0].status, "taxi")
        self.assertEqual(ir.designation_delta.moved_to_ir[0].status, "ir")
        self.assertEqual(blocked.status, "blocked")

    def test_cross_team_player_is_not_available_to_scenario(self):
        result = ScenarioSimulatorService(FakeClient()).simulate(context(), [TradePlayerOut(player_name="Cross League Trap")])

        self.assertEqual(result.status, "blocked")
        self.assertIn("not verified on this roster", result.conflicts[0])

    def test_batch_read_profile_stays_bounded_for_199_rows(self):
        client = FakeClient()
        client.rows["contracts"] = [
            {"league_id": "league-1", "owner_name": "Owner One", "player_name": f"Player {idx}", "player_position": "WR", "sleeper_player_id": f"p{idx}", "salary": 1, "contract_years_left": 1}
            for idx in range(199)
        ]

        result = ScenarioSimulatorService(client).simulate(context(), [ReleasePlayer(player_name="Player 1")])

        self.assertIn("Player 1", result.summary())
        self.assertLessEqual(len(client.selects), 18)

    def test_simulator_source_has_no_supabase_mutation_calls(self):
        source = inspect.getsource(simulator_source)
        for forbidden in (".insert(", ".update(", ".upsert(", ".delete(", ".rpc("):
            self.assertNotIn(forbidden, source)

    def test_pipeline_routes_scenario_to_direct_fact_without_recommendation(self):
        result = run_assistant_pipeline(
            context=context(),
            question="What happens if I cut Garrett Wilson?",
            retrieval_provider=SupabaseEvidenceRetrievalProvider(FakeClient()),
            interpreter_sb=FakeClient(),
            owner_preferences={},
            team_context={},
        )

        self.assertEqual(result.interpreted_question.primary_intent, "scenario_simulation")
        self.assertEqual(result.decision_plan.plan_type, "scenario_simulation_plan")
        self.assertEqual([request.retrieval_type for request in result.decision_plan.retrieval_requests], ["scenario_simulation"])
        self.assertEqual(result.decision_plan.calculation_requests, [])
        self.assertEqual(result.decision_output.decision_type, "factual_response")
        self.assertEqual(result.answer_packet.answer_mode, "direct_fact")
        self.assertIsNone(result.answer_packet.recommendation)
        self.assertIn("Garrett Wilson", result.displayed_answer)
        self.assertNotIn("validated recommendation", result.displayed_answer.lower())


if __name__ == "__main__":
    unittest.main()
