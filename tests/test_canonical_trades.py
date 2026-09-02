from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from services.canonical_trades import (
    DraftPickMovement,
    DraftInventorySlot,
    PlayerMovement,
    RetainedSalary,
    RetainedSeason,
    draft_season_window,
    execute_canonical_trade,
    initialize_draft_inventory,
    is_draft_pick_tradeable,
    team_selection_options,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeRpc:
    def __init__(self, client, name, params):
        self.client = client
        self.name = name
        self.params = params

    def execute(self):
        self.client.calls.append((self.name, self.params))
        if self.name == "initialize_draft_inventory_authenticated":
            return FakeResponse({"season": 2027, "created_asset_count": 30, "idempotent": False})
        return FakeResponse({"trade_id": "trade-1", "status": "completed"})


class FakeClient:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        return FakeRpc(self, name, params)


class CanonicalTradeTest(unittest.TestCase):
    def execute(self, participant_count=2, **updates):
        client = FakeClient()
        participants = tuple(f"team-{index}" for index in range(participant_count))
        values = {
            "league_id": "league-1",
            "participant_team_ids": participants,
            "idempotency_key": "trade-key",
            "player_movements": (PlayerMovement("contract-1", "team-0", "team-1"),),
        }
        values.update(updates)
        result = execute_canonical_trade(client, **values)
        return client, result

    def test_two_three_and_four_team_trades_use_one_rpc(self):
        for count in (2, 3, 4):
            with self.subTest(count=count):
                client, result = self.execute(count)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(client.calls), 1)
                self.assertEqual(len(client.calls[0][1]["p_request"]["participant_team_ids"]), count)

    def test_player_pick_and_retained_salary_share_one_atomic_request(self):
        client, _result = self.execute(
            3,
            draft_pick_movements=(DraftPickMovement("pick-1", "team-1", "team-2"),),
            retained_salary=(RetainedSalary(
                "contract-1", "team-0", "team-1",
                (RetainedSeason(2026, Decimal("10")), RetainedSeason(2027, Decimal("8"))),
            ),),
        )
        request = client.calls[0][1]["p_request"]
        self.assertEqual(len(request["player_movements"]), 1)
        self.assertEqual(len(request["draft_pick_movements"]), 1)
        self.assertEqual(request["retained_salary"][0]["seasons"][1], {"season": 2027, "amount": "8"})

    def test_invalid_participant_counts_and_retention_horizon_fail_before_rpc(self):
        for count in (1, 5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.execute(count)
        with self.assertRaises(ValueError):
            self.execute(retained_salary=(RetainedSalary(
                "contract-1", "team-0", "team-1",
                tuple(RetainedSeason(2026 + offset, Decimal("1")) for offset in range(5)),
            ),))

    def test_dynamic_three_season_display_window(self):
        self.assertEqual(draft_season_window(2026), (2026, 2027, 2028))
        self.assertEqual(draft_season_window(2027), (2027, 2028, 2029))

    def test_lifecycle_status_alone_controls_pick_eligibility(self):
        self.assertTrue(is_draft_pick_tradeable("scheduled", "tradable"))
        self.assertTrue(is_draft_pick_tradeable("in_progress", "tradable"))
        self.assertFalse(is_draft_pick_tradeable("completed", "tradable"))
        self.assertFalse(is_draft_pick_tradeable("scheduled", "historical"))
        self.assertFalse(is_draft_pick_tradeable("in_progress", "consumed"))

    def test_migration_defines_atomic_authorities_and_guards(self):
        source = (ROOT / "supabase/migrations/20261103_canonical_atomic_trade_engine.sql").read_text()
        for fragment in (
            "create table public.league_draft_lifecycles",
            "unique(league_id,season)",
            "status in('scheduled','in_progress','completed')",
            "create table public.trade_retained_salary_obligations",
            "create or replace function public.complete_rookie_draft_authenticated",
            "create or replace function public.execute_canonical_trade_authenticated",
            "retained_salary_exceeds_contract_cap_hit",
            "trade_pick_lifecycle_not_tradeable",
            "update public.contract_agreements set league_team_id=to_id",
            "update public.contract_seasons set league_team_id=to_id",
        ):
            self.assertIn(fragment, source)
        self.assertNotIn("update public.season_roster_assignments", source)
        self.assertNotIn("update public.team_roster_state", source)
        self.assertNotIn("cap_adjustments", source)

    def test_pgcrypto_digest_is_explicitly_schema_qualified(self):
        source = (ROOT / "supabase/migrations/20261103_canonical_atomic_trade_engine.sql").read_text()
        self.assertIn("extensions.digest(", source)
        self.assertNotRegex(source, r"(?<![.\w])digest\(")
        self.assertIn("set search_path=pg_catalog,public", source)

    def test_completion_requires_exact_reconciled_inventory(self):
        source = (ROOT / "supabase/migrations/20261103_canonical_atomic_trade_engine.sql").read_text()
        for rejection in (
            "draft_completion_expected_pick_count_invalid",
            "draft_completion_pick_count_mismatch",
            "draft_completion_asset_count_mismatch",
            "draft_completion_asset_slot_duplicate",
            "draft_completion_asset_slot_missing",
            "draft_completion_asset_cross_league",
            "draft_completion_assignment_asset_mismatch",
            "draft_completion_assignment_without_asset",
        ):
            self.assertIn(rejection, source)
        self.assertIn("set asset_status='historical'", source)
        self.assertIn("rookie_draft_assignment_id", source)
        self.assertIn("if r.status='completed' then return", source)

    def test_inventory_initializer_is_complete_idempotent_and_preserves_ownership(self):
        source = (ROOT / "supabase/migrations/20261103_canonical_atomic_trade_engine.sql").read_text()
        self.assertIn("create or replace function public.initialize_draft_inventory_authenticated", source)
        self.assertIn("draft_inventory_slot_count_mismatch", source)
        self.assertIn("draft_inventory_slot_duplicate", source)
        self.assertIn("draft_inventory_slot_missing", source)
        self.assertIn("draft_inventory_cross_league_team", source)
        self.assertIn("draft_inventory_replay_conflict", source)
        self.assertIn("completed_draft_lifecycle_immutable", source)
        replay = source[source.index("if asset.stable_pick_id is null then"):source.index("if(select count(*) from public.draft_pick_assets")]
        self.assertNotIn("set current_owner_league_team_id", replay)

    def test_inventory_facade_sends_explicit_ownership_and_provenance(self):
        client = FakeClient()
        result = initialize_draft_inventory(
            client,
            league_id="league-1",
            season=2027,
            slots=(DraftInventorySlot(1, "original", "current", {"source": "sleeper", "evidence_id": "pick-1"}),),
        )
        self.assertEqual(result["created_asset_count"], 30)
        request = client.calls[0][1]["p_request"]["slots"][0]
        self.assertEqual(request["original_league_team_id"], "original")
        self.assertEqual(request["current_owner_league_team_id"], "current")
        self.assertEqual(request["provenance"]["evidence_id"], "pick-1")

    def test_same_name_teams_remain_distinct_uuid_options(self):
        options = team_selection_options((
            {"id": "aaaaaaaa-1111", "team_name": "Same", "owner_name": "Owner"},
            {"id": "bbbbbbbb-2222", "team_name": "Same", "owner_name": "Owner"},
        ))
        self.assertEqual([value for value, _label in options], ["aaaaaaaa-1111", "bbbbbbbb-2222"])
        self.assertEqual(len({label for _value, label in options}), 2)

    def test_trade_idempotency_is_serialized_before_lookup(self):
        source = (ROOT / "supabase/migrations/20261103_canonical_atomic_trade_engine.sql").read_text()
        lock = source.index("canonical-trade:")
        lookup = source.index("select * into trade from public.canonical_trades")
        self.assertLess(lock, lookup)
        self.assertIn("pg_advisory_xact_lock", source[lock - 80:lock + 120])

    def test_rollover_patch_targets_current_set_based_cap_helpers(self):
        migration = (ROOT / "supabase/migrations/20261103_canonical_atomic_trade_engine.sql").read_text()
        current = (ROOT / "supabase/migrations/20261017_phaseD_set_based_prepared_team_caps.sql").read_text()
        self.assertIn("phase3b10b_derive_team_caps_private(uuid,uuid,uuid,numeric,text)", migration)
        for fragment in (
            "select roster_row.league_team_id,season_row.salary::numeric as amount",
            "select agreement_row.league_team_id,season_row.cap_hit::numeric as amount",
            "), dead_by_team as (",
            "perform 1 from public.rollover_dead_cap_obligations where rollover_execution_id=execution_row.id",
        ):
            self.assertIn(fragment, migration)
            self.assertIn(fragment, current)
        self.assertNotIn("r.assignment_set_id=aset.id", migration)


if __name__ == "__main__":
    unittest.main()
