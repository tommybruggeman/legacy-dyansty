from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

auth_stub = types.ModuleType("auth")
auth_stub.service_client = lambda: None
sys.modules.setdefault("auth", auth_stub)

state_mod = importlib.import_module("gm_assistant.conversation_state")
request_context = importlib.import_module("gm_assistant.request_context")
brain_context = importlib.import_module("gm_assistant.brain_context")
openai_service = importlib.import_module("gm_assistant.openai_service")


def make_context(
    *,
    user_id="user-1",
    league_id="league-1",
    league_team_id="team-1",
    conversation_id=None,
):
    return request_context.AssistantRequestContext(
        user_id=user_id,
        league_id=league_id,
        league_team_id=league_team_id,
        membership_id=f"membership-{user_id}",
        role="owner",
        current_season=2026,
        requested_season=2026,
        conversation_id=conversation_id,
        permission_scopes=(
            request_context.TEAM_ADVICE,
            request_context.LEAGUE_PUBLIC_READ,
        ),
        team_name="Team One",
        owner_name="Owner One",
    )


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name, action="select", payload=None, on_conflict=None):
        self.client = client
        self.table_name = table_name
        self.action = action
        self.payload = payload
        self.on_conflict = on_conflict
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
        if self.client.fail_table == self.table_name:
            raise RuntimeError("database unavailable")
        if self.action == "upsert":
            self.client.upserts.append((self.table_name, dict(self.payload), self.on_conflict))
            return Result([self.payload])

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

    def upsert(self, payload, on_conflict=None):
        return FakeQuery(self.client, self.table_name, action="upsert", payload=payload, on_conflict=on_conflict)


class FakeClient:
    def __init__(self):
        self.fail_table = None
        self.upserts = []
        self.rows = {
            "league_memberships": [
                {
                    "id": "membership-user-1",
                    "user_id": "user-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_id": None,
                    "role": "owner",
                },
                {
                    "id": "membership-co-owner",
                    "user_id": "co-owner",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_id": None,
                    "role": "co_owner",
                },
            ],
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Team One"},
            ],
            "gm_user_memory": [
                {
                    "user_id": "user-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_name": "Team One",
                    "current_focus": "compete",
                    "notes": ["explicit_preference:do_not_trade_first_round_pick"],
                },
                {
                    "user_id": "co-owner",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "team_name": "Team One",
                    "current_focus": "rebuild",
                    "notes": ["explicit_preference:prefers_younger_players"],
                },
            ],
        }

    def table(self, table_name):
        return FakeTable(self, table_name)


class ConversationStateIsolationTest(unittest.TestCase):
    def test_state_is_scoped_by_user_league_team_and_conversation(self):
        session = {}
        ctx = make_context()
        other_user = make_context(user_id="user-2")
        other_league = make_context(league_id="league-2")
        other_team = make_context(league_team_id="team-2")

        state = state_mod.load_conversation_state(ctx, session, conversation_id="c1")
        self.assertEqual(state.conversation_id, "c1")
        self.assertNotEqual(
            state_mod.conversation_state_key(ctx, "c1"),
            state_mod.conversation_state_key(other_user, "c1"),
        )
        self.assertNotEqual(
            state_mod.conversation_state_key(ctx, "c1"),
            state_mod.conversation_state_key(other_league, "c1"),
        )
        self.assertNotEqual(
            state_mod.conversation_state_key(ctx, "c1"),
            state_mod.conversation_state_key(other_team, "c1"),
        )
        self.assertNotEqual(
            state_mod.conversation_state_key(ctx, "c1"),
            state_mod.conversation_state_key(ctx, "c2"),
        )

    def test_coowners_do_not_share_personal_state(self):
        session = {}
        owner_state = state_mod.load_conversation_state(make_context(user_id="user-1"), session, conversation_id="c1")
        co_owner_state = state_mod.load_conversation_state(make_context(user_id="co-owner"), session, conversation_id="c1")

        owner_state.active_objective = "compete"
        state_mod.save_conversation_state(make_context(user_id="user-1"), session, owner_state)

        self.assertIsNone(co_owner_state.active_objective)

    def test_mismatched_state_is_rejected(self):
        state = state_mod.create_conversation_state(make_context(user_id="user-1"), "c1")
        with self.assertRaises(state_mod.ConversationStateError):
            state_mod.validate_conversation_state(make_context(user_id="user-2"), state)


class ConversationStateUpdateTest(unittest.TestCase):
    def setUp(self):
        self.ctx = make_context()
        self.state = state_mod.create_conversation_state(self.ctx, "c1")

    def test_objective_set_replaced_and_cleared(self):
        state_mod.update_conversation_state(
            self.state,
            state_mod.ConversationStateUpdate(replace_objective="compete"),
        )
        self.assertEqual(self.state.active_objective, "compete")

        state_mod.update_conversation_state(
            self.state,
            state_mod.ConversationStateUpdate(replace_objective="rebuild"),
        )
        self.assertEqual(self.state.active_objective, "rebuild")

        state_mod.update_conversation_state(
            self.state,
            state_mod.ConversationStateUpdate(clear_objective=True),
        )
        self.assertIsNone(self.state.active_objective)

    def test_timeframe_constraint_players_rejections_scenario_and_ambiguity(self):
        update = state_mod.ConversationStateUpdate(
            replace_timeframe="next_season",
            add_player_ids=["p1", "p1"],
            add_constraints={"do_not_trade_first_round_pick": True},
            add_rejected_options=[{"option": "trade_a"}],
            replace_current_scenario={"type": "trade", "version": 1},
            add_ambiguities=["current_player_reference"],
        )
        state_mod.update_conversation_state(self.state, update)

        self.assertEqual(self.state.active_timeframe, "next_season")
        self.assertEqual(self.state.discussed_player_ids, ["p1"])
        self.assertTrue(self.state.constraints["do_not_trade_first_round_pick"])
        self.assertEqual(self.state.rejected_options, [{"option": "trade_a"}])
        self.assertEqual(self.state.current_scenario["version"], 1)
        self.assertEqual(self.state.unresolved_ambiguities, ["current_player_reference"])

        state_mod.update_conversation_state(
            self.state,
            state_mod.ConversationStateUpdate(
                remove_constraint_keys=["do_not_trade_first_round_pick"],
                clear_rejected_options=True,
                replace_current_scenario={"type": "trade", "version": 2},
                resolve_ambiguities=["current_player_reference"],
            ),
        )

        self.assertNotIn("do_not_trade_first_round_pick", self.state.constraints)
        self.assertEqual(self.state.rejected_options, [])
        self.assertEqual(self.state.current_scenario["version"], 2)
        self.assertEqual(self.state.unresolved_ambiguities, [])

    def test_reset_returns_clean_state(self):
        self.state.active_objective = "compete"
        self.state.constraints["x"] = True
        reset = state_mod.update_conversation_state(
            self.state,
            state_mod.ConversationStateUpdate(reset_state=True, last_message_id="m1"),
        )

        self.assertIsNone(reset.active_objective)
        self.assertEqual(reset.constraints, {})
        self.assertEqual(reset.last_message_id, "m1")


class FollowUpFoundationTest(unittest.TestCase):
    def test_single_discussed_player_can_be_current_reference(self):
        state = state_mod.create_conversation_state(make_context(), "c1")
        state.discussed_player_ids = ["p1"]

        self.assertEqual(state_mod.resolve_current_player_reference(state), "p1")

    def test_multiple_discussed_players_produce_ambiguity(self):
        state = state_mod.create_conversation_state(make_context(), "c1")
        state.discussed_player_ids = ["p1", "p2"]

        self.assertIsNone(state_mod.resolve_current_player_reference(state))
        self.assertIn("current_player_reference", state.unresolved_ambiguities)

    def test_inferred_updates_replace_objective_constraint_and_scenario(self):
        state = state_mod.create_conversation_state(make_context(), "c1")
        state_mod.update_conversation_state(
            state,
            state_mod.infer_conversation_state_update_from_text("I do not want to move my first."),
        )
        self.assertTrue(state.constraints["do_not_trade_first_round_pick"])

        state_mod.update_conversation_state(
            state,
            state_mod.infer_conversation_state_update_from_text("Actually, I want to rebuild instead."),
        )
        self.assertEqual(state.active_objective, "rebuild")

        state_mod.update_conversation_state(
            state,
            state_mod.infer_conversation_state_update_from_text("I am willing to move my first now."),
        )
        self.assertNotIn("do_not_trade_first_round_pick", state.constraints)

        state_mod.update_conversation_state(
            state,
            state_mod.infer_conversation_state_update_from_text("What if I include my second?"),
        )
        self.assertEqual(state.current_scenario["type"], "hypothetical_trade")

    def test_start_over_clears_active_state(self):
        state = state_mod.create_conversation_state(make_context(), "c1")
        state.active_objective = "compete"

        state = state_mod.update_conversation_state(
            state,
            state_mod.infer_conversation_state_update_from_text("Start over."),
        )

        self.assertIsNone(state.active_objective)


class DurableMemoryBoundaryTest(unittest.TestCase):
    def test_personal_memory_requires_matching_user_league_and_team(self):
        client = FakeClient()

        owner = brain_context.load_gm_memory(
            "Team One",
            user_id="user-1",
            league_id="league-1",
            league_team_id="team-1",
            sb=client,
        )
        co_owner = brain_context.load_gm_memory(
            "Team One",
            user_id="co-owner",
            league_id="league-1",
            league_team_id="team-1",
            sb=client,
        )

        self.assertEqual(owner["current_focus"], "compete")
        self.assertEqual(co_owner["current_focus"], "rebuild")

    def test_missing_memory_is_empty_but_retrieval_failure_is_distinguishable(self):
        client = FakeClient()

        missing = brain_context.load_gm_memory(
            "Team One",
            user_id="missing",
            league_id="league-1",
            league_team_id="team-1",
            sb=client,
        )
        self.assertIsNone(missing["current_focus"])

        client.fail_table = "gm_user_memory"
        failed = brain_context.load_gm_memory(
            "Team One",
            user_id="user-1",
            league_id="league-1",
            league_team_id="team-1",
            sb=client,
        )
        self.assertEqual(failed["memory_load_error"], "retrieval_failed")

    def test_explicit_preference_can_be_represented(self):
        fields = state_mod.durable_memory_fields_from_text(
            "Do not recommend trading my first-round pick."
        )

        self.assertIn("explicit_preference:do_not_trade_first_round_pick", fields["notes"])

    def test_temporary_scenario_and_assistant_inference_are_not_preferences(self):
        self.assertEqual(
            state_mod.durable_memory_fields_from_text("What if I trade my first?"),
            {},
        )
        self.assertEqual(
            state_mod.durable_memory_fields_from_text("The assistant thinks you should rebuild."),
            {},
        )

    def test_openai_memory_write_only_for_explicit_user_preference(self):
        client = FakeClient()
        identity = brain_context.AssistantIdentity(
            team_name="Team One",
            user_id="user-1",
            league_id="league-1",
            league_team_id="team-1",
        )

        openai_service._store_conversation_memory(
            sb=client,
            identity=identity,
            question="Should I trade my first?",
            answer="No.",
        )
        self.assertEqual(client.upserts, [])

        openai_service._store_conversation_memory(
            sb=client,
            identity=identity,
            question="Do not recommend trading my first-round pick.",
            answer="Got it.",
        )
        self.assertEqual(len(client.upserts), 1)
        self.assertEqual(client.upserts[0][2], "user_id,league_id,league_team_id")


class OpenAIConversationStateCompatibilityTest(unittest.TestCase):
    def test_structured_state_is_sent_without_replacing_raw_question(self):
        state = state_mod.create_conversation_state(make_context(), "c1")
        state.active_objective = "rebuild"

        messages = openai_service._build_initial_messages(
            "What about him next year?",
            [{"role": "assistant", "content": "Prior answer"}],
            state,
        )

        self.assertIn("Structured conversation state", messages[0]["content"])
        self.assertEqual(messages[-1]["content"], "What about him next year?")

    def test_gm_assistant_page_uses_conversation_state_and_scoped_history(self):
        source = (ROOT / "pages" / "05_GM_Assistant.py").read_text()

        self.assertIn("load_conversation_state", source)
        self.assertIn("AssistantRuntime", source)
        self.assertIn("AssistantRuntimeInput", source)
        self.assertIn("conversation_state=conversation_state", source)
        self.assertIn("gm_messages:{user_id}:{league_id}:{league_team_id}:{conversation_id}", source)
        self.assertNotIn("interpret_question(", source)


if __name__ == "__main__":
    unittest.main()
