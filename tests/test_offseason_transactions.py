import unittest
from unittest.mock import Mock
from pathlib import Path

from services.free_agents import RookieRow
from services.offseason_transactions import (
    calculate_default_dead_cap,
    OffseasonTransactionService,
    resolve_auction_terms,
    resolve_normal_free_agent_terms,
    resolve_rookie_contract_terms,
    scale_rookie_salary,
    resolve_waiver_terms,
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
        client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"season": 2026, "status": "active", "is_active": True}
        ]
        OffseasonTransactionService(client, "league").release(
            player_id="p", league_team_id="t", dead_cap=4,
            idempotency_key="drop:p",
        )
        self.assertEqual(client.rpc.call_args.args[0], "release_offseason_player_authenticated")
        self.assertEqual(client.rpc.call_args.args[1]["p_request"]["season"], 2026)


class CanonicalTransactionRuleTests(unittest.TestCase):
    def test_rookie_scale_off_and_unchanged_cap_use_base(self):
        rules = {"rookie_scale_enabled": True, "scale_rookie_salaries_with_cap": False,
                 "salary_cap": 250, "rookie_salary_scale_base_cap": 225}
        self.assertEqual(resolve_rookie_contract_terms(rules, 1, 3).salary, 9)
        rules["scale_rookie_salaries_with_cap"] = True
        rules["salary_cap"] = 225
        self.assertEqual(resolve_rookie_contract_terms(rules, 1, 3).salary, 9)

    def test_rookie_scale_increase_decrease_rounding_and_no_compounding(self):
        rules = {"rookie_scale_enabled": True, "scale_rookie_salaries_with_cap": True,
                 "salary_cap": 250, "rookie_salary_scale_base_cap": 225}
        self.assertEqual(resolve_rookie_contract_terms(rules, 1, 3).salary, 10)
        self.assertEqual(scale_rookie_salary(12, 250), 13)
        self.assertEqual(scale_rookie_salary(18, 250), 20)
        rules["salary_cap"] = 200
        self.assertEqual(resolve_rookie_contract_terms(rules, 1, 3).salary, 8)
        rules["salary_cap"] = 275
        self.assertEqual(resolve_rookie_contract_terms(rules, 1, 3).salary, 11)

    def test_rookie_terms_fail_closed_and_ignore_fa_defaults(self):
        rules = {"rookie_scale_enabled": True, "scale_rookie_salaries_with_cap": False,
                 "default_fa_salary": 99, "default_fa_years": 5}
        terms = resolve_rookie_contract_terms(rules, 3, 1)
        self.assertEqual((terms.salary, terms.years, terms.option_salary), (1, 1, 7))
        with self.assertRaisesRegex(ValueError, "missing"):
            resolve_rookie_contract_terms(rules, 4, 1)

    def test_normal_waiver_auction_and_dead_cap_rules(self):
        rules = {"league_min_salary": 1, "default_fa_salary": 99,
                 "max_contract_years": 4, "min_2_year_bid": 4,
                 "min_3_year_bid": 12, "min_4_year_bid": 20,
                 "year_discount_pct": 10, "default_dead_cap_pct": 50}
        self.assertEqual(resolve_normal_free_agent_terms(rules), resolve_waiver_terms(rules, 0))
        self.assertEqual(resolve_waiver_terms(rules, 7).salary, 7)
        self.assertEqual(resolve_auction_terms(rules, 12, 3).salary, 12)
        with self.assertRaisesRegex(ValueError, "minimum"):
            resolve_auction_terms(rules, 11, 3)
        self.assertEqual(calculate_default_dead_cap(rules, 9), 4.50)


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

    def test_rule_authority_migration_is_replay_safe_and_private(self):
        sql = Path("supabase/migrations/20261028_league_transaction_rule_authority.sql").read_text().lower()
        for fragment in ("add column if not exists scale_rookie_salaries_with_cap",
                         "rookie_salary_scale_base_cap", "default false",
                         "resolve_rookie_contract_terms_private",
                         "resolve_offseason_contract_terms_private",
                         "calculate_default_drop_dead_cap_private",
                         "round(base_salary*multiplier,0)", "revoke all"):
            self.assertIn(fragment, sql)


if __name__ == "__main__":
    unittest.main()
