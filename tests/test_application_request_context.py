from __future__ import annotations

import unittest

from services.application_request_context import (
    ApplicationContextResolver,
    ContextFailureCode,
    ContextRequest,
)


class FakeIdentityRepository:
    def __init__(self, memberships=(), teams=(), fail=False):
        self.memberships = list(memberships)
        self.teams = {row["id"]: row for row in teams}
        self.fail = fail

    def list_memberships(self, user_id):
        if self.fail:
            raise RuntimeError("database secret detail")
        return [row for row in self.memberships if row.get("user_id") == user_id]

    def list_memberships_for_user_and_league(self, user_id, league_id):
        if self.fail:
            raise RuntimeError("database secret detail")
        return [row for row in self.memberships if row.get("user_id") == user_id and row.get("league_id") == league_id]

    def get_league_team(self, league_team_id):
        if self.fail:
            raise RuntimeError("database secret detail")
        return self.teams.get(league_team_id)


def canonical_membership(**updates):
    row = {"id": "membership-1", "user_id": "user-1", "league_id": "league-1", "league_team_id": "team-1", "team_id": None, "role": "owner"}
    row.update(updates)
    return row


class ApplicationRequestContextTest(unittest.TestCase):
    def resolve(self, repository, **updates):
        request = {"authenticated_user_id": "user-1", "active_league_id": "league-1"}
        request.update(updates)
        return ApplicationContextResolver(repository).resolve(ContextRequest(**request))

    def test_no_authenticated_user(self):
        result = self.resolve(FakeIdentityRepository(), authenticated_user_id=None)
        self.assertEqual(result.failure.code, ContextFailureCode.UNAUTHENTICATED)

    def test_missing_active_league_and_multiple_memberships_never_selects_first(self):
        repo = FakeIdentityRepository([
            canonical_membership(),
            canonical_membership(id="membership-2", league_id="league-2", league_team_id="team-2"),
        ])
        result = self.resolve(repo, active_league_id=None)
        self.assertEqual(result.failure.code, ContextFailureCode.NO_ACTIVE_LEAGUE_SELECTED)
        self.assertTrue(result.failure.diagnostics["multiple_memberships"])
        self.assertEqual(result.failure.diagnostics["membership_count"], 2)

    def test_membership_not_found(self):
        repo = FakeIdentityRepository([canonical_membership(league_id="league-2")])
        result = self.resolve(repo)
        self.assertEqual(result.failure.code, ContextFailureCode.MEMBERSHIP_NOT_FOUND)

    def test_duplicate_memberships_are_rejected_without_selecting_one(self):
        repo = FakeIdentityRepository([canonical_membership(), canonical_membership(id="membership-2")])
        result = self.resolve(repo)
        self.assertEqual(result.failure.code, ContextFailureCode.DUPLICATE_MEMBERSHIP)
        self.assertEqual(result.failure.diagnostics["membership_count"], 2)

    def test_malformed_membership_is_invalid_context(self):
        class MalformedRepository(FakeIdentityRepository):
            def list_memberships_for_user_and_league(self, user_id, league_id):
                return [canonical_membership(user_id="user-2")]

        result = self.resolve(MalformedRepository())
        self.assertEqual(result.failure.code, ContextFailureCode.INVALID_CONTEXT)

    def test_league_team_from_another_league(self):
        repo = FakeIdentityRepository([canonical_membership()], [{"id": "team-1", "league_id": "league-2"}])
        result = self.resolve(repo)
        self.assertEqual(result.failure.code, ContextFailureCode.LEAGUE_TEAM_MISMATCH)

    def test_league_team_not_found(self):
        result = self.resolve(FakeIdentityRepository([canonical_membership()]))
        self.assertEqual(result.failure.code, ContextFailureCode.LEAGUE_TEAM_NOT_FOUND)

    def test_valid_canonical_league_team_id(self):
        repo = FakeIdentityRepository([canonical_membership()], [{"id": "team-1", "league_id": "league-1"}])
        result = self.resolve(repo, season=2026)
        self.assertTrue(result.ok)
        self.assertEqual(result.context.league_team_id, "team-1")
        self.assertFalse(result.context.provenance.legacy_fallback_used)
        self.assertEqual(result.context.season, 2026)

    def test_isolated_legacy_fallback_is_diagnostic(self):
        membership = canonical_membership(league_team_id=None, team_id="legacy-team")
        repo = FakeIdentityRepository([membership], [{"id": "legacy-team", "league_id": "league-1"}])
        result = self.resolve(repo)
        self.assertTrue(result.ok)
        self.assertTrue(result.context.provenance.legacy_fallback_used)
        self.assertEqual(result.context.provenance.league_team_id, "legacy_membership.team_id")

    def test_legacy_fallback_can_fail_closed(self):
        membership = canonical_membership(league_team_id=None, team_id="legacy-team")
        result = self.resolve(FakeIdentityRepository([membership]), allow_legacy_team_id=False)
        self.assertEqual(result.failure.code, ContextFailureCode.LEGACY_IDENTITY_REQUIRED)

    def test_owner_name_is_never_authorization_identity(self):
        membership = canonical_membership(league_team_id=None, team_id=None, owner_name="Display Owner")
        repo = FakeIdentityRepository([membership], [{"id": "team-by-name", "league_id": "league-1", "owner_name": "Display Owner"}])
        result = self.resolve(repo)
        self.assertEqual(result.failure.code, ContextFailureCode.LEGACY_IDENTITY_REQUIRED)

    def test_typed_backend_failure_is_safe(self):
        result = self.resolve(FakeIdentityRepository(fail=True))
        self.assertEqual(result.failure.code, ContextFailureCode.BACKEND_UNAVAILABLE)
        self.assertEqual(result.failure.diagnostics["exception_type"], "RuntimeError")
        self.assertNotIn("secret", str(result.failure.diagnostics).lower())


if __name__ == "__main__":
    unittest.main()
