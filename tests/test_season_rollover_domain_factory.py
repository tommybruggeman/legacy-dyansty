from __future__ import annotations

import unittest

from tests.fixtures.season_rollover_domain_factory import (
    BOOTSTRAP_TABLE_ALLOWLIST,
    CANONICAL_MEMBERSHIP_ROLES,
    LEGACY_MEMBERSHIP_ROLES,
    LIFECYCLE_TABLE_DENYLIST,
    SeasonRolloverDomainFactory,
)


class SeasonRolloverDomainFactoryTests(unittest.TestCase):
    def test_domain_population_is_canonical_and_deterministic(self):
        first = SeasonRolloverDomainFactory("unit")
        second = SeasonRolloverDomainFactory("unit")
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(len(first.identity.team_ids), 10)
        self.assertEqual(len(first.identity.owner_player_ids), 3)
        self.assertEqual(len(first.identity.commissioner_player_ids), 13)
        source = first.history_source()
        self.assertEqual(len(source["rosters"]), 10)
        self.assertEqual(sum(len(row["players"]) for row in source["rosters"]),
                         len(first.identity.roster_player_ids))
        sql = first.bootstrap_sql()
        self.assertIn(
            "rookie_class_year,draft_year,draft_round,is_rookie_contract",
            sql,
        )

    def test_abs_shaped_profile_has_authoritative_classification_counts(self):
        identity = SeasonRolloverDomainFactory("abs-shaped", profile="abs_shaped").identity
        self.assertEqual({key: len(value) for key, value in identity.lifecycle_player_ids.items()}, {
            "ordinary_continuing": 74,
            "ordinary_expiration": 113,
            "rookie_initial_continuing": 12,
            "rookie_initial_taxi_paused": 9,
            "rookie_option_eligible": 3,
        })
        self.assertEqual(sum(map(len, identity.lifecycle_player_ids.values())), 211)
        self.assertEqual(len(identity.owner_player_ids), 3)

    def test_memberships_use_only_canonical_roles_and_team_authority(self):
        factory = SeasonRolloverDomainFactory("membership-roles")
        identity = factory.identity
        membership_inserts = [statement for statement in factory.bootstrap_sql().split(";\n")
                              if statement.startswith("insert into public.league_memberships")]
        self.assertEqual(len(membership_inserts), 2 + len(identity.owner_ids))
        self.assertIn("'commissioner',null", membership_inserts[0])
        owner_memberships = membership_inserts[1:1 + len(identity.owner_ids)]
        self.assertTrue(all("'member'" in statement for statement in owner_memberships))
        self.assertTrue(all(team_id in statement
                            for team_id, statement in zip(identity.team_ids, owner_memberships)))
        self.assertFalse(any(f"'{role}'" in statement for role in LEGACY_MEMBERSHIP_ROLES
                             for statement in membership_inserts))
        self.assertEqual(CANONICAL_MEMBERSHIP_ROLES, {"commissioner", "member"})

    def test_foreign_owner_is_a_distinct_member_of_another_league(self):
        home = SeasonRolloverDomainFactory("home")
        self.assertNotEqual(home.identity.league_id, home.identity.foreign_league_id)
        self.assertNotEqual(home.identity.owner_id, home.identity.outsider_id)
        foreign_membership = next(statement for statement in home.bootstrap_sql().split(";\n")
            if statement.startswith("insert into public.league_memberships")
            and home.identity.outsider_id in statement)
        self.assertIn(home.identity.foreign_league_id, foreign_membership)
        self.assertIn(home.identity.foreign_team_id, foreign_membership)
        self.assertIn("'member'", foreign_membership)

    def test_external_auth_actors_are_not_manually_inserted(self):
        actors = ("10000000-0000-0000-0000-000000000001",
                  "10000000-0000-0000-0000-000000000002",
                  "10000000-0000-0000-0000-000000000003")
        factory = SeasonRolloverDomainFactory("external-auth", commissioner_id=actors[0],
            owner_id=actors[1], outsider_id=actors[2], externally_provisioned_actor_ids=actors)
        auth_inserts = [statement for statement in factory.bootstrap_sql().split(";\n")
                        if statement.startswith("insert into auth.users")]
        self.assertEqual(len(auth_inserts), 9)
        self.assertFalse(any(actor in statement for actor in actors for statement in auth_inserts))

    def test_schema_preflight_enforces_current_membership_check(self):
        class Session:
            def json_query(self, _sql):
                return {"membership_role_check": "CHECK ((lower(role) = ANY (ARRAY['commissioner'::text, 'member'::text])))",
                    "membership_columns": {name: {} for name in
                        ("id", "league_id", "user_id", "role", "league_team_id")},
                    "league_season_columns": {name: {} for name in
                        ("id", "league_id", "season", "sleeper_league_id", "is_active", "status",
                         "previous_league_season_id")}, "table_constraints": {}}
        report = SeasonRolloverDomainFactory.assert_hosted_schema_compatibility(Session())
        self.assertIn("commissioner", report["membership_role_check"])

    def test_schema_preflight_rejects_missing_canonical_membership_role(self):
        class Session:
            def json_query(self, _sql):
                return {
                    "membership_role_check": "CHECK (role IN ('commissioner','owner'))",
                    "membership_columns": {
                        "id": {},
                        "league_id": {},
                        "user_id": {},
                        "role": {},
                        "league_team_id": {},
                    },
                    "league_season_columns": {
                        "id": {},
                        "league_id": {},
                        "season": {},
                        "sleeper_league_id": {},
                        "is_active": {},
                        "status": {},
                        "previous_league_season_id": {},
                    },
                    "table_constraints": {},
                }

        with self.assertRaisesRegex(RuntimeError, "missing canonical roles"):
            SeasonRolloverDomainFactory.assert_hosted_schema_compatibility(Session())


    def test_bootstrap_is_allowlisted_and_contains_no_lifecycle_write(self):
        factory = SeasonRolloverDomainFactory("audit")
        writes = set(factory.audit_bootstrap_sql(factory.bootstrap_sql()))
        self.assertTrue(writes)
        self.assertTrue(writes.issubset(BOOTSTRAP_TABLE_ALLOWLIST))
        self.assertFalse(writes.intersection(LIFECYCLE_TABLE_DENYLIST))

    def test_auditor_rejects_lifecycle_insert(self):
        with self.assertRaises(AssertionError):
            SeasonRolloverDomainFactory.audit_bootstrap_sql(
                "insert into public.rollover_executions(id) values ('x')"
            )


if __name__ == "__main__":
    unittest.main()
