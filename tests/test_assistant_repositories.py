from __future__ import annotations

import inspect
import unittest

from gm_assistant.evidence import SupabaseEvidenceRetrievalProvider
from gm_assistant.planning import RetrievalRequest
from gm_assistant.repositories import (
    CapRepository,
    ContractRepository,
    DraftPickRepository,
    PlayerRepository,
    RosterRepository,
    TeamRepository,
)
from gm_assistant.request_context import AssistantRequestContext, LEAGUE_PUBLIC_READ, TEAM_ADVICE


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.limit_value = None

    def select(self, cols="*"):
        self.client.selects.append((self.table_name, cols, self.filters))
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def in_(self,key,values):self.filters.append(("__in__",(key,{str(x) for x in values})));return self

    def execute(self):
        if self.table_name in self.client.fail_tables:
            raise RuntimeError(f"{self.table_name} failed")
        rows = list(self.client.rows.get(self.table_name, []))
        for key, value in self.filters:
            if key=="__in__":rows=[row for row in rows if str(row.get(value[0])) in value[1]]
            else:rows = [row for row in rows if str(row.get(key)) == str(value)]
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
        self.fail_tables = set()
        self.selects = []
        self.rows = {
            "league_teams": [
                {"id": "team-1", "league_id": "league-1", "team_name": "Condor Dynasty", "owner_name": "Owner One"},
                {"id": "team-2", "league_id": "league-1", "team_name": "Rival Team", "owner_name": "Owner Two"},
                {"id": "team-x", "league_id": "league-2", "team_name": "Cross League", "owner_name": "Cross Owner"},
            ],
            "leagues": [{"id": "league-1", "name": "Legacy League"}],
            "team_roster_state": [],
            "contracts": [
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Josh Allen", "player_position": "QB", "sleeper_player_id": "4984", "salary": 48, "contract_years_left": 3},
                {"league_id": "league-1", "owner_name": "Owner One", "player_name": "Released Player", "player_position": "RB", "sleeper_player_id": "cut", "status": "released", "salary": 1, "contract_years_left": 1},
                {"league_id": "league-1", "owner_name": "Owner Two", "player_name": "Cross Team", "player_position": "WR", "sleeper_player_id": "cross-team", "salary": 10, "contract_years_left": 2},
                {"league_id": "league-2", "owner_name": "Cross Owner", "player_name": "Cross League", "player_position": "WR", "sleeper_player_id": "cross-league", "salary": 99, "contract_years_left": 2},
            ],
            "league_seasons":[{"id":"ls26","league_id":"league-1","season":2026,"is_active":True,"status":"active"}],
            "contract_transition_executions":[],
            "contract_agreements":[{"id":"a1","league_id":"league-1","league_team_id":"team-1","player_id":"4984","sleeper_player_id":"4984","status":"active","end_season":2028,"contract_type":"veteran","source_legacy_contract_id":"c1"},{"id":"a2","league_id":"league-1","league_team_id":"team-1","player_id":"cut","sleeper_player_id":"cut","status":"expired","end_season":2025,"contract_type":"veteran","source_legacy_contract_id":"c2"},{"id":"a3","league_id":"league-1","league_team_id":"team-2","player_id":"cross-team","sleeper_player_id":"cross-team","status":"active","end_season":2027,"contract_type":"veteran","source_legacy_contract_id":"c3"}],
            "contract_seasons":[{"id":"s1","contract_id":"a1","league_id":"league-1","league_team_id":"team-1","player_id":"4984","season":2026,"salary":48,"obligation_status":"active"},{"id":"s2","contract_id":"a1","league_id":"league-1","league_team_id":"team-1","player_id":"4984","season":2027,"salary":48,"obligation_status":"scheduled"},{"id":"s3","contract_id":"a1","league_id":"league-1","league_team_id":"team-1","player_id":"4984","season":2028,"salary":48,"obligation_status":"scheduled"},{"id":"s4","contract_id":"a2","league_id":"league-1","league_team_id":"team-1","player_id":"cut","season":2025,"salary":1,"obligation_status":"satisfied"},{"id":"s5","contract_id":"a3","league_id":"league-1","league_team_id":"team-2","player_id":"cross-team","season":2026,"salary":10,"obligation_status":"active"},{"id":"s6","contract_id":"a3","league_id":"league-1","league_team_id":"team-2","player_id":"cross-team","season":2027,"salary":10,"obligation_status":"scheduled"}],
            "contract_events":[],"player_universe":[{"sleeper_id":"4984","player_name":"Josh Allen","pos":"QB"},{"sleeper_id":"cut","player_name":"Released Player","pos":"RB"},{"sleeper_id":"cross-team","player_name":"Cross Team","pos":"WR"}],
            "season_roster_assignments":[{"league_season_id":"ls26","league_team_id":"team-1","sleeper_player_id":"4984"},{"league_season_id":"ls26","league_team_id":"team-1","sleeper_player_id":"cut"}],"free_agents":[],
            "league_rules": [{"league_id": "league-1", "salary_cap": 100, "roster_limit": 22}],
            "cap_adjustments": [{"league_id": "league-1", "owner_name": "Owner One", "season": 2026, "adjustment_type": "dropped_player_charge", "amount": 2}],
            "v_team_caps": [{"league_id": "league-1", "league_team_id": "team-1", "season": 2026, "available_cap": 50}],
            "draft_picks": [
                {"league_id": "league-1", "season": 2026, "round": 1, "original_pick_rank": 2, "pick_label": "1.02", "current_owner": "Owner One", "original_team": "Owner Two"},
                {"league_id": "league-1", "season": 2026, "round": 2, "original_pick_rank": 1, "pick_label": "2.01", "current_owner": "Owner Two", "original_team": "Owner One"},
                {"league_id": "league-2", "season": 2026, "round": 1, "original_pick_rank": 1, "pick_label": "1.01", "current_owner": "Cross Owner", "original_team": "Cross Owner"},
            ],
            "team_brain": [{"league_id": "league-1", "league_team_id": "team-1", "team_name": "Condor Dynasty", "position_needs": ["RB"]}],
            "league_brain": [{"league_id": "league-1", "season": 2026, "league_size": 10}],
            "league_settings": [],
            "player_strategic_profiles": [{"league_id": "league-1", "league_team_id": "team-1", "sleeper_id": "4984", "player_name": "Josh Allen"}],
            "league_relative_player_values": [{"league_id": "league-1", "league_team_id": "team-1", "sleeper_id": "4984", "value_score": 95}],
            "player_intelligence": [{"sleeper_id": "4984", "player_name": "Josh Allen", "global_grade": "elite"}],
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


class AssistantRepositoryTest(unittest.TestCase):
    def test_repositories_do_not_import_streamlit_or_rendering(self):
        import gm_assistant.repositories.roster as roster
        import gm_assistant.repositories.contracts as contracts
        import gm_assistant.repositories.cap as cap
        import gm_assistant.repositories.draft_picks as draft_picks
        import gm_assistant.repositories.players as players

        for module in (roster, contracts, cap, draft_picks, players):
            source = inspect.getsource(module)
            self.assertNotIn("import streamlit", source.lower())
            self.assertNotIn("rendered_answer", source)
            self.assertNotIn("answer_packet", source)

    def test_roster_repository_uses_canonical_team_then_contract_fallback(self):
        result = RosterRepository(FakeClient()).get_team_roster(make_context(), league_team_id="team-1")

        self.assertTrue(result.ok)
        self.assertEqual(result.source.source_name, "contracts")
        self.assertEqual(result.source.scope, "team")
        self.assertEqual([row["player_name"] for row in result.rows], ["Josh Allen"])
        self.assertEqual(result.rows[0]["league_team_id"], "team-1")

    def test_contract_repository_filters_by_team_without_cross_team_leakage(self):
        client=FakeClient();client.gm_contract_read_mode="normalized";result = ContractRepository(client).get_contracts(make_context(), league_team_ids=["team-1"])

        self.assertEqual([row["player_name"] for row in result.rows], ["Josh Allen", "Released Player"])
        self.assertTrue(all(row["league_id"] == "league-1" for row in result.rows))
        self.assertNotIn("Cross Team", [row["player_name"] for row in result.rows])

    def test_cap_repository_computes_fallback_when_view_unavailable(self):
        client = FakeClient()
        client.fail_tables.add("v_team_caps")

        result = CapRepository(client).get_cap_summary(make_context(), league_team_id="team-1")

        self.assertEqual(result.source.source_name, "contracts/cap_adjustments/league_rules")
        self.assertEqual(result.rows[0]["active_salary"], 49.0)
        self.assertEqual(result.rows[0]["available_cap"], 49.0)

    def test_draft_pick_repository_resolves_production_owner_fields(self):
        result = DraftPickRepository(FakeClient()).get_draft_picks(make_context(), league_team_id="team-1", seasons=[2026])

        self.assertEqual([row["pick_label"] for row in result.rows], ["1.02", "2.01"])
        self.assertEqual(result.rows[0]["resolved_current_owner_team_id"], "team-1")
        self.assertEqual(result.rows[0]["resolved_original_team_id"], "team-2")

    def test_player_repository_global_intelligence_has_no_league_filter(self):
        client = FakeClient()
        result = PlayerRepository(client).get_global_player_intelligence(make_context(), player_ids=["4984"])

        self.assertEqual(result.source.scope, "global")
        self.assertIsNone(result.source.league_id)
        self.assertEqual(result.rows[0]["player_name"], "Josh Allen")
        player_intel_selects = [item for item in client.selects if item[0] == "player_intelligence"]
        self.assertTrue(player_intel_selects)
        self.assertEqual(player_intel_selects[0][2], [])

    def test_player_repository_scoped_profiles_merge_relative_values(self):
        result = PlayerRepository(FakeClient()).get_scoped_player_profiles(make_context(), player_ids=["4984"])

        self.assertEqual(result.source.scope, "team")
        self.assertEqual(result.rows[0]["player_name"], "Josh Allen")
        self.assertEqual(result.rows[0]["value_score"], 95)

    def test_team_repository_identity_fallback_when_team_brain_missing(self):
        client = FakeClient()
        client.rows["team_brain"] = []

        result = TeamRepository(client).get_team_brain(make_context(), league_team_id="team-1")

        self.assertEqual(result.source.source_name, "league_teams")
        self.assertEqual(result.rows[0]["team_name"], "Condor Dynasty")
        self.assertEqual(result.rows[0]["league_name"], "Legacy League")

    def test_evidence_provider_adds_safe_lineage(self):
        request = RetrievalRequest(
            retrieval_type="team_contracts",
            scope="team",
            reason="contract lookup",
            required=True,
            team_ids=["team-1"],
        )

        client=FakeClient();client.gm_contract_read_mode="normalized";provider_result = SupabaseEvidenceRetrievalProvider(client).get_player_contracts(make_context(), request)

        self.assertEqual(provider_result.source_name, "normalized_contract_model")
        self.assertEqual(provider_result.lineage[0].domain, "contracts")
        self.assertEqual(provider_result.lineage[0].scope, "team")
        self.assertEqual(provider_result.lineage[0].league_id, "league-1")
        self.assertEqual(provider_result.lineage[0].league_team_id, "team-1")
        self.assertNotIn("select", repr(provider_result.lineage[0]).lower())


if __name__ == "__main__":
    unittest.main()
