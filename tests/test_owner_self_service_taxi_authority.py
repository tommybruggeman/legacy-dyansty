from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SQL = (ROOT / "supabase/migrations/20261106_owner_self_service_taxi_authority.sql").read_text().lower()
UI = (ROOT / "pages/02_My_Team.py").read_text()
ROLLOVER = (ROOT / "supabase/migrations/20261020_abs_2025_rookie_taxi_contract_reconciliation.sql").read_text().lower()
UNLOCK = (ROOT / "supabase/migrations/20260830_phase3b8b_taxi_assignment_unlock.sql").read_text().lower()


class OwnerSelfServiceTaxiAuthorityTests(unittest.TestCase):
    def test_owner_is_scoped_through_canonical_membership_team(self):
        for fragment in (
            "public.require_authenticated_user()",
            "from public.league_memberships",
            "m.user_id = actor",
            "actor_team_id = tid",
            "taxi team authority required",
        ):
            self.assertIn(fragment, SQL)

    def test_commissioner_override_is_preserved(self):
        self.assertIn("commissioner', 'admin', 'host", SQL)
        self.assertIn("commissioner_override", SQL)

    def test_one_locked_slot_is_a_database_invariant(self):
        self.assertIn("unique index if not exists rookie_taxi_one_slot_per_team_season", SQL)
        self.assertIn("league_season_id, league_team_id", SQL)
        self.assertIn("taxi slot is locked for this team and season", SQL)
        self.assertIn("pg_advisory_xact_lock", SQL)

    def test_current_rookie_provenance_and_ownership_fail_closed(self):
        for fragment in (
            "rookie_draft_board_assignments",
            "b.draft_year = season_year",
            "b.rookie_contract_provenance",
            "taxi canonical ownership mismatch",
        ):
            self.assertIn(fragment, SQL)
        self.assertNotIn("sleeper_players", SQL)

    def test_charge_is_canonical_and_taxi_is_half_salary(self):
        self.assertIn("from public.contract_seasons", SQL)
        self.assertIn("coalesce(normal, board.original_salary)", SQL)
        self.assertIn("requested_normal is distinct from normal", SQL)
        self.assertIn("round(normal * 0.50, 2)", SQL)

    def test_taxi_year_is_explicitly_not_consumed(self):
        self.assertIn("contract_year_consumed, locked", SQL)
        self.assertIn("normal, round(normal * 0.50, 2), false, true", SQL)
        self.assertIn("classification='rookie_initial_taxi_paused'", ROLLOVER)
        self.assertIn("'scheduled',false,null,'abs_2025_taxi_preserved_initial_contract_v1'", ROLLOVER)

    def test_rollover_unlocks_without_automatic_return_or_ir_mutation(self):
        self.assertIn("unlock_target_season", SQL)
        self.assertIn("target_roster_designation", UNLOCK)
        self.assertIn("automatic_taxi_return',false", UNLOCK)
        self.assertIn("'ir_rows_mutated',0", UNLOCK)

    def test_ui_uses_the_single_authenticated_rpc_and_displays_discount(self):
        self.assertIn(".assign_rookie_taxi(", UI)
        self.assertIn("adjustment_amount = round(taxi_charge - salary, 2)", UI)
        self.assertNotIn('rpc("persist_rookie_taxi_assignment_authenticated"', UI)


if __name__ == "__main__":
    unittest.main()
