from pathlib import Path
import unittest

from season_engine.target_authority import TargetAuthorityRepository
from tests.test_contract_reads import Client

ROOT=Path(__file__).resolve().parents[1]
SQL=(ROOT/"supabase/migrations/20260803_target_season_authority.sql").read_text()
TABLES=("league_rollover_policies","free_agent_publications","dead_cap_obligations","dead_cap_season_authorities","cap_season_authorities")

class MigrationValidityTests(unittest.TestCase):
    def test_expected_tables_constraints_indexes_rls_grants_and_triggers(self):
        lowered=SQL.lower()
        for table in TABLES:
            self.assertIn(f"create table if not exists public.{table}",lowered)
            self.assertIn(table+"'",lowered)
        for phrase in ("primary key","unique(","check(","enable row level security","grant all on table","grant select on table","create policy","create trigger"):
            self.assertIn(phrase,lowered)
    def test_no_data_mutation_or_destructive_existing_object_statement(self):
        lowered=SQL.lower()
        for phrase in ("insert into","truncate ","drop table","delete from","update public.league_seasons","update public.contracts","alter table public.contracts"):
            self.assertNotIn(phrase,lowered)
    def test_partial_repeat_is_handled_for_tables_indexes_policies_and_triggers(self):
        lowered=SQL.lower();self.assertEqual(lowered.count("create table if not exists public."),5)
        self.assertIn("create index if not exists",lowered);self.assertIn("drop policy if exists",lowered);self.assertIn("drop trigger if exists",lowered)
    def test_migration_has_no_authority_initialization_or_publication_rpc(self):
        lowered=SQL.lower()
        self.assertNotIn("publish_free_agent(",lowered);self.assertNotIn("initialize_dead_cap",lowered);self.assertNotIn("activate_cap_authority",lowered)

class EmptyAuthorityReadTests(unittest.TestCase):
    def client(self):
        client=Client()
        for table in TABLES:client.rows[table]=[]
        return client
    def test_empty_schema_is_uninitialized_never_authoritative(self):
        repo=TargetAuthorityRepository(self.client())
        self.assertEqual(repo.policy_state("l1",2026)["status"],"missing")
        self.assertEqual(repo.publication_state("l1",2026)["published_count"],0)
        self.assertEqual(repo.dead_cap_state("l1",2026)["status"],"uninitialized")
        self.assertFalse(repo.dead_cap_state("l1",2026)["authoritative_zero"])
        self.assertEqual(repo.cap_state("l1",2026)["status"],"uninitialized")
        self.assertFalse(repo.cap_state("l1",2026)["authoritative"])

if __name__=="__main__":unittest.main()
