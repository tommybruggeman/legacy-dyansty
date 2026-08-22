import hashlib
import json
import pathlib
import unittest
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20261017_phaseD_set_based_prepared_team_caps.sql"


def _validate(team_ids, sources):
    if not team_ids or len(team_ids) != len(set(team_ids)):
        raise ValueError("canonical team set invalid")
    expected = set(team_ids)
    for source in sources:
        if any(team not in expected for team, _ in source):
            raise ValueError("foreign or substituted team")


def old_loop_rows(team_ids, rostered, preserved, dead, cap=Decimal("225.00")):
    _validate(team_ids, (rostered, preserved, dead))
    rows = []
    for team in sorted(team_ids):
        active_values = [amount for source in (rostered, preserved) for key, amount in source if key == team]
        dead_values = [amount for key, amount in dead if key == team]
        active = sum(active_values, Decimal("0.00"))
        dead_cap = sum(dead_values, Decimal("0.00"))
        rows.append((team, cap, active, dead_cap, active + dead_cap, cap - active - dead_cap,
                     len(active_values), len(dead_values)))
    return rows


def set_based_rows(team_ids, rostered, preserved, dead, cap=Decimal("225.00")):
    _validate(team_ids, (rostered, preserved, dead))
    active_sum, active_count, dead_sum, dead_count = {}, {}, {}, {}
    for team, amount in rostered + preserved:
        active_sum[team] = active_sum.get(team, Decimal("0.00")) + amount
        active_count[team] = active_count.get(team, 0) + 1
    for team, amount in dead:
        dead_sum[team] = dead_sum.get(team, Decimal("0.00")) + amount
        dead_count[team] = dead_count.get(team, 0) + 1
    return [(team, cap, active_sum.get(team, Decimal("0.00")), dead_sum.get(team, Decimal("0.00")),
             active_sum.get(team, Decimal("0.00")) + dead_sum.get(team, Decimal("0.00")),
             cap - active_sum.get(team, Decimal("0.00")) - dead_sum.get(team, Decimal("0.00")),
             active_count.get(team, 0), dead_count.get(team, 0)) for team in sorted(team_ids)]


def diagnostic_fingerprint(rows):
    material = [[str(v) if isinstance(v, Decimal) else v for v in row] for row in rows]
    return hashlib.sha256(json.dumps(material, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class PhaseDPreparedCapsTests(unittest.TestCase):
    def test_old_loop_and_set_math_match_all_cardinalities(self):
        for size in (1, 10, 32, 100, 2000):
            teams = [f"team-{n:04d}" for n in range(size)]
            rostered = [(team, Decimal(n % 23)) for n, team in enumerate(teams)]
            preserved = [(team, Decimal("1.50")) for n, team in enumerate(teams) if n % 7 == 0]
            dead = [(team, Decimal("2.25")) for n, team in enumerate(teams) if n % 11 == 0]
            old = old_loop_rows(teams, rostered, preserved, dead)
            new = set_based_rows(list(reversed(teams)), list(reversed(rostered)), preserved, dead)
            self.assertEqual(old, new)
            self.assertEqual(diagnostic_fingerprint(old), diagnostic_fingerprint(new))

    def test_zero_duplicate_foreign_and_equal_count_substitution_fail(self):
        with self.assertRaises(ValueError): set_based_rows([], [], [], [])
        with self.assertRaises(ValueError): set_based_rows(["a", "a"], [], [], [])
        with self.assertRaises(ValueError): set_based_rows(["a", "b"], [("foreign", Decimal(1))], [], [])
        with self.assertRaises(ValueError): set_based_rows(["a", "b"], [("c", Decimal(1)), ("a", Decimal(1))], [], [])

    def test_migration_is_transactional_private_and_set_based(self):
        sql = MIGRATION.read_text()
        self.assertTrue(sql.lstrip().startswith("begin;"))
        self.assertTrue(sql.rstrip().endswith("commit;"))
        self.assertNotRegex(sql.lower(), r"for\s+\w+\s+in\s+select")
        self.assertIn("insert into public.prepared_team_caps", sql.lower())
        self.assertIn("from jsonb_array_elements(cap_rows)", sql.lower())
        self.assertIn("cap_assignment_fingerprint_mismatch", sql.lower())
        self.assertIn("cap_snapshot_mismatch", sql.lower())
        self.assertIn("revoke all on function public.phase3b10b_derive_team_caps_private", sql.lower())
        self.assertNotIn("create index", sql.lower())
        self.assertNotIn("update public.contract", sql.lower())
        self.assertNotIn("delete from public", sql.lower())

    def test_existing_v1_financial_and_fingerprint_fields_are_preserved(self):
        sql = MIGRATION.read_text()
        for token in ("'team'", "'cap'", "'active'", "'dead'", "'contracts'",
                      "'obligations'", "'assignment_hash'", "phase3b10b-cap-v1"):
            self.assertIn(token, sql)

    def test_round_trip_count_is_constant_in_team_cardinality(self):
        # One helper invocation, one cap-set insert, and one cap-row INSERT SELECT.
        sql = MIGRATION.read_text().lower()
        self.assertEqual(sql.count("phase3b10b_derive_team_caps_private(execution_row.id"), 1)
        self.assertEqual(sql.count("insert into public.prepared_team_cap_sets"), 1)
        self.assertEqual(sql.count("insert into public.prepared_team_caps"), 1)


if __name__ == "__main__":
    unittest.main()
