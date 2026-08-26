import unittest
from unittest.mock import Mock
from pathlib import Path

from services.free_agents import RookieRow
from services.offseason_transactions import (
    OffseasonTransactionService,
    rookie_draft_player_options,
    taxi_eligible_player_names,
)


class OffseasonReadAuthorityTests(unittest.TestCase):
    def test_rookie_board_uses_exact_free_agent_rookie_rows(self):
        rows = (RookieRow("r1", "Eligible Rookie", "RB", "DEN", 2, 40, 8, "Round 2, Pick 40", "Boise", "DRAFTED"),)
        self.assertEqual(rookie_draft_player_options(rows), ("Eligible Rookie — RB (r1)",))

    def test_taxi_requires_team_roster_and_draft_provenance(self):
        roster = [
            {"sleeper_player_id": "drafted", "player": "Drafted Rookie"},
            {"sleeper_player_id": "auction", "player": "Auction Rookie"},
            {"sleeper_player_id": "manual", "player": "Manual Rookie"},
            {"sleeper_player_id": "veteran", "player": "Veteran"},
        ]
        assignments = [
            {"player_id": "drafted", "original_league_team_id": "mine", "draft_year": 2026, "rookie_contract_provenance": True},
            {"player_id": "other", "original_league_team_id": "other-team", "draft_year": 2026, "rookie_contract_provenance": True},
        ]
        self.assertEqual(taxi_eligible_player_names(roster, assignments, league_team_id="mine"), ("Drafted Rookie",))


class OffseasonWriteBoundaryTests(unittest.TestCase):
    def test_auction_and_manual_add_use_canonical_acquisition_rpc(self):
        response = Mock(data={"contract_agreement_id": "agreement"})
        execute = Mock(return_value=response)
        client = Mock()
        client.rpc.return_value.execute = execute
        service = OffseasonTransactionService(client, "league")
        service.acquire(player_id="p", league_team_id="t", season=2026, salary=8, years=2,
                        acquisition_type="fa_auction", idempotency_key="auction:p")
        request = client.rpc.call_args.args
        self.assertEqual(request[0], "acquire_offseason_player_authenticated")
        self.assertEqual(request[1]["p_request"]["acquisition_type"], "fa_auction")

    def test_drop_uses_canonical_release_rpc(self):
        client = Mock()
        client.rpc.return_value.execute.return_value.data = {"contract_agreement_id": "agreement"}
        OffseasonTransactionService(client, "league").release(
            player_id="p", league_team_id="t", season=2026, dead_cap=4,
            idempotency_key="drop:p",
        )
        self.assertEqual(client.rpc.call_args.args[0], "release_offseason_player_authenticated")


class OffseasonMigrationTests(unittest.TestCase):
    def test_migration_is_atomic_canonical_and_duplicate_safe(self):
        sql = Path("supabase/migrations/20261027_offseason_canonical_acquisition_release.sql").read_text().lower()
        for fragment in ("begin;", "contract_agreements", "contract_seasons", "contract_events",
                         "player already has canonical active ownership", "assert_no_active_rollover_cutover_lock",
                         "persist_rookie_draft_board_authenticated", "rookie_draft_board_assignments",
                         "acquire_offseason_player_private", "request_fingerprint",
                         "complete canonical contract season schedule required",
                         "rookie draft assignment conflict", "dead_cap_created",
                         "obligation_status=case"):
            self.assertIn(fragment, sql)
        self.assertNotIn("insert into public.contracts", sql)
        self.assertNotIn("insert into public.team_roster_state", sql)


if __name__ == "__main__":
    unittest.main()
