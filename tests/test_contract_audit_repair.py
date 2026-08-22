from pathlib import Path
import unittest


SQL=(Path(__file__).parents[1]/"supabase/migrations/20260802_repair_contract_source_correction_audit.sql").read_text()


class ContractAuditRepairMigrationTest(unittest.TestCase):
    def test_inserts_exactly_two_and_is_idempotent(self):
        self.assertIn("on conflict (idempotency_key) do nothing",SQL.lower())
        self.assertIn("<> 2",SQL)
        self.assertEqual(SQL.count("contract-source-correction:luke-musgrave-9481:v1"),1)
        self.assertEqual(SQL.count("contract-source-correction:tyler-allgeier-8132:v1"),1)
    def test_never_modifies_contracts(self):
        lowered=SQL.lower()
        self.assertNotIn("insert into public.contracts",lowered)
        self.assertNotIn("update public.contracts",lowered)
        self.assertNotIn("delete from public.contracts",lowered)
    def test_requires_survivors_and_deleted_rows_absent(self):
        self.assertIn("both erroneous contract rows to remain absent",SQL)
        self.assertIn("Canonical Luke Musgrave row is absent",SQL)
        self.assertIn("Canonical Tyler Allgeier row is absent",SQL)
    def test_conflicting_existing_content_fails(self):
        self.assertIn("Conflicting preexisting Luke Musgrave audit record",SQL)
        self.assertIn("Conflicting preexisting Tyler Allgeier audit record",SQL)
    def test_exact_deleted_snapshots_are_literal(self):
        self.assertIn('"player_name":"Like Musgrave"',SQL)
        self.assertIn('"owner_name":"Connor Cassidy"',SQL)
        self.assertIn('"created_at":"2026-06-02T04:38:05.696448+00:00"',SQL)
    def test_atomic_and_search_path_controlled(self):
        self.assertTrue(SQL.lstrip().startswith("-- Phase 3A"))
        self.assertIn("begin;",SQL.lower())
        self.assertIn("commit;",SQL.lower())
        self.assertIn("set local search_path = pg_catalog, public",SQL.lower())

if __name__=="__main__": unittest.main()
