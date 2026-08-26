from __future__ import annotations

from copy import deepcopy
import unittest

from season_engine.contract_authority_preflight import ContractAuthorityPreflightService
from tests.test_contract_reads import Client


def client() -> Client:
    value = Client()
    value.rows = {
        "league_seasons": [
            {"id": "s25", "league_id": "l", "season": 2025, "is_active": True, "status": "active"},
            {"id": "s26", "league_id": "l", "season": 2026, "is_active": False, "status": "scheduled"},
        ],
        "contract_agreements": [
            {"id": "a", "league_id": "l", "league_team_id": "t", "player_id": "p", "status": "expired", "start_season": 2025, "end_season": 2026},
        ],
        "contract_seasons": [
            {"id": "c25", "contract_id": "a", "league_id": "l", "league_team_id": "t", "player_id": "p", "season": 2025, "salary": 5, "cap_hit": 5, "obligation_status": "satisfied", "is_option_year": False, "option_type": None},
            {"id": "c26", "contract_id": "a", "league_id": "l", "league_team_id": "t", "player_id": "p", "season": 2026, "salary": 5, "cap_hit": 5, "obligation_status": "scheduled", "is_option_year": True, "option_type": "owner_option"},
        ],
        "season_roster_assignments": [{"league_season_id": "s25", "sleeper_player_id": "p"}],
        "contract_transition_executions": [],
        "contract_rollover_classifications": [],
        "contract_transition_reconciliations": [],
        "rookie_draft_board_assignments": [],
    }
    return value


class ContractAuthorityPreflightTests(unittest.TestCase):
    def test_valid_preexecution_authority_is_read_only_and_stable(self):
        c = client(); before = deepcopy(c.rows); service = ContractAuthorityPreflightService(c)
        first = service.run("l", 2025, 2026); second = service.run("l", 2025, 2026)
        self.assertTrue(first.ready); self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.prepared_target_option_count, 1); self.assertEqual(c.rows, before)

    def test_missing_option_duplicate_and_ownership_conflict_block(self):
        c = client(); c.rows["contract_seasons"] = c.rows["contract_seasons"][:1]
        self.assertIn("prepared_target_option_missing:a", ContractAuthorityPreflightService(c).run("l", 2025, 2026).blockers)
        c = client(); c.rows["contract_seasons"].append(dict(c.rows["contract_seasons"][0], id="duplicate"))
        self.assertIn("contract_source_obligation_count:a:2", ContractAuthorityPreflightService(c).run("l", 2025, 2026).blockers)
        c = client(); c.rows["contract_seasons"][0]["league_team_id"] = "wrong"
        self.assertIn("contract_source_ownership_mismatch:a", ContractAuthorityPreflightService(c).run("l", 2025, 2026).blockers)

    def test_stale_material_activation_and_legacy_execution_rejected(self):
        c = client(); service = ContractAuthorityPreflightService(c); original = service.run("l", 2025, 2026)
        c.rows["contract_seasons"][1]["salary"] = 6
        self.assertNotEqual(original.fingerprint, service.run("l", 2025, 2026).fingerprint)
        c.rows["contract_seasons"][1]["obligation_status"] = "active"
        self.assertIn("target_contract_authority_already_activated", service.run("l", 2025, 2026).blockers)
        c = client(); c.rows["contract_transition_executions"] = [{"league_id": "l", "source_season": 2025, "target_season": 2026}]
        self.assertIn("prior_contract_transition_conflicts_with_rollover", ContractAuthorityPreflightService(c).run("l", 2025, 2026).blockers)

    def test_incoming_target_rookie_uses_board_authority_without_source_classification(self):
        c = client()
        c.rows["contract_agreements"].append({
            "id": "incoming", "league_id": "l", "league_team_id": "t", "player_id": "r",
            "status": "scheduled", "start_season": 2026, "end_season": 2026,
            "origin": "signed", "contract_type": "rookie",
        })
        c.rows["contract_seasons"].append({
            "id": "incoming-26", "contract_id": "incoming", "league_id": "l",
            "league_team_id": "t", "player_id": "r", "season": 2026,
            "salary": 2, "cap_hit": 2, "obligation_status": "scheduled",
            "is_option_year": False, "option_type": None,
        })
        c.rows["rookie_draft_board_assignments"].append({
            "league_id": "l", "draft_year": 2026, "player_id": "r",
            "original_league_team_id": "t", "rookie_contract_provenance": True,
        })
        result = ContractAuthorityPreflightService(c).run("l", 2025, 2026)
        self.assertTrue(result.ready)
        self.assertEqual(result.agreement_count, 1)
        self.assertEqual(result.source_season_count, 1)

        c.rows["rookie_draft_board_assignments"][0]["original_league_team_id"] = "wrong"
        blocked = ContractAuthorityPreflightService(c).run("l", 2025, 2026)
        self.assertIn("contract_source_obligation_count:incoming:0", blocked.blockers)


if __name__ == "__main__": unittest.main()
