from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SQL = (ROOT / "supabase/migrations/20261110_owner_self_service_ir_authority.sql").read_text().lower()
RLS = (ROOT / "supabase/migrations/20261111_cap_adjustment_team_authority.sql").read_text().lower()
UI = (ROOT / "pages/02_My_Team.py").read_text()
SERVICE = (ROOT / "services/offseason_transactions.py").read_text()


class OwnerSelfServiceIrAuthorityTests(unittest.TestCase):
    def test_ui_routes_ir_through_authenticated_service_rpc(self):
        self.assertIn(".assign_injured_reserve(", UI)
        self.assertNotIn('"POST",\n                        "cap_adjustments"', UI)
        self.assertIn('"persist_injured_reserve_assignment_authenticated"', SERVICE)

    def test_owner_and_commissioner_authority_are_canonical(self):
        for fragment in (
            "public.require_authenticated_user()",
            "from public.league_memberships",
            "m.user_id = actor",
            "actor_team_id = tid",
            "commissioner', 'admin', 'host",
            "ir team authority required",
        ):
            self.assertIn(fragment, SQL)

    def test_league_season_team_and_roster_must_match(self):
        for fragment in (
            "ls.league_id = lid",
            "ls.is_active",
            "lt.league_id = lid",
            "from public.season_roster_assignments",
            "sra.league_season_id = sid",
            "sra.league_team_id = tid",
            "sra.sleeper_player_id = pid",
        ):
            self.assertIn(fragment, SQL)

    def test_ir_slot_and_cap_effect_are_preserved(self):
        self.assertIn("adjustment_type = 'ir_adjustment'", SQL)
        self.assertIn("ir slot is already occupied", SQL)
        self.assertIn("from public.contract_seasons", SQL)
        self.assertIn("requested_normal is distinct from normal", SQL)
        self.assertIn("adjustment := round(-(normal / 2), 2)", SQL)

    def test_rpc_privilege_excludes_anon(self):
        self.assertIn("from public, anon, authenticated, service_role", SQL)
        self.assertIn("to authenticated", SQL)

    def test_direct_cap_adjustment_writes_cannot_bypass_team_authority(self):
        self.assertNotIn("with check (true)", RLS)
        self.assertNotIn("using (true)", RLS)
        self.assertIn("m.user_id = auth.uid()", RLS)
        self.assertIn("m.league_team_id = t.id", RLS)
        self.assertIn("commissioner', 'admin', 'host", RLS)
        self.assertIn("for insert to authenticated", RLS)
        self.assertIn("for update to authenticated", RLS)
        self.assertIn("for delete to authenticated", RLS)


if __name__ == "__main__":
    unittest.main()
