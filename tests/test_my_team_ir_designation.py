from pathlib import Path
import unittest

from services.team_roster_state import invalidate_team_state_session_cache, roster_designation


UI = (Path(__file__).parents[1] / "pages/02_My_Team.py").read_text()


class MyTeamIrDesignationTests(unittest.TestCase):
    def test_uncached_state_loader_is_never_cleared(self):
        # load_canonical_team_state is a plain function; .clear() raised
        # AttributeError when a manager clicked Remove on IR.
        self.assertNotIn("load_canonical_team_state.clear()", UI)
        self.assertIn("invalidate_team_state_session_cache(st.session_state)", UI)

    def test_ir_removal_runs_as_signed_in_manager_and_checks_rows(self):
        self.assertNotIn('"DELETE",\n                            "cap_adjustments"', UI)
        self.assertIn('.table("cap_adjustments")\n            .delete()', UI)
        self.assertIn("if not deleted:", UI)

    def test_ir_dropdown_excludes_taxi_and_designated_players(self):
        self.assertIn("roster_designation(r) is None", UI)
        self.assertIn("str(r.get(\"player\")) not in designated_names", UI)

    def test_selected_designation_shows_current_holder(self):
        # Taxi and IR each have their own card that names the current holder.
        self.assertIn('roster_designation(r) == held_designation', UI)
        self.assertIn('designation_holders("Taxi Squad")', UI)
        self.assertIn('designation_holders("Injured Reserve")', UI)

    def test_epoch_bump_changes_state_cache_key(self):
        state = {}
        self.assertEqual(invalidate_team_state_session_cache(state), 1)
        self.assertEqual(invalidate_team_state_session_cache(state), 2)

    def test_taxi_player_is_not_ir_eligible(self):
        self.assertEqual(roster_designation({"roster_designation": "Taxi"}), "taxi")
        self.assertIsNone(roster_designation({"roster_designation": None}))


if __name__ == "__main__":
    unittest.main()
