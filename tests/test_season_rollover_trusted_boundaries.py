from pathlib import Path
import unittest

from season_engine.history import DeterministicHistorySource
from season_engine.models import LeagueSeason


ROOT = Path(__file__).parents[1]
SQL = (ROOT / "supabase/migrations/20260922_season_rollover_trusted_boundaries.sql").read_text()
CONTROL = (ROOT / "services/season_rollover_control.py").read_text()
PAGE = (ROOT / "pages/90_Settings.py").read_text()


class TrustedBoundaryStructureTests(unittest.TestCase):
    def test_policy_wrapper_derives_actor_and_private_function_is_ungrantable(self):
        self.assertIn("actor uuid:=auth.uid()", SQL)
        self.assertIn("exactly one canonical commissioner membership required", SQL)
        self.assertIn("revoke all on function public.approve_canonical_rollover_policy_private(jsonb,uuid) from public,anon,authenticated,service_role", SQL)
        self.assertIn("grant execute on function public.approve_canonical_rollover_policy_authenticated(jsonb) to authenticated", SQL)

    def test_policy_rejects_spoofable_and_noncanonical_material(self):
        self.assertIn("p_request - array", SQL)
        self.assertNotIn("trusted_actor_user_id", SQL)
        self.assertIn("changed-material policy replay rejected", SQL)
        self.assertIn("SEVEN_CALENDAR_DAYS_AFTER_OFFICIAL_COMMISSIONER_ROLLOVER_NOTICE", SQL)

    def test_page_never_constructs_service_role_client(self):
        for forbidden in ("SUPABASE_SERVICE_ROLE_KEY", "service_client_factory", "create_client("):
            self.assertNotIn(forbidden, PAGE)
        self.assertNotIn("self.service_client", CONTROL)


class DeterministicHistorySourceTests(unittest.TestCase):
    def fixture(self):
        return {"league": {"league_id": "sl", "settings": {"last_scored_leg": 0}},
                "users": [], "rosters": [], "matchups_by_week": {},
                "winners_bracket": [], "losers_bracket": []}

    def test_explicit_disposable_gate_and_deterministic_replay(self):
        with self.assertRaisesRegex(RuntimeError, "disposable"):
            DeterministicHistorySource(self.fixture())
        source = DeterministicHistorySource(self.fixture(), disposable=True)
        season = LeagueSeason(id="s", league_id="l", season=2025, is_active=True,
                              sleeper_league_id="sl")
        self.assertEqual(source.fetch(season), source.fetch(season))

    def test_fixture_must_match_authoritative_sleeper_identity(self):
        source = DeterministicHistorySource(self.fixture(), disposable=True)
        season = LeagueSeason(id="s", league_id="l", season=2025, is_active=True,
                              sleeper_league_id="other")
        with self.assertRaisesRegex(ValueError, "does not match"):
            source.fetch(season)


if __name__ == "__main__": unittest.main()
