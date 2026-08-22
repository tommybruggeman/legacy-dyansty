import unittest

from contract_engine.reconciliation import is_typo_only_duplicate, material_differences, reconcile_owner_evidence, select_spelling_canonical


def row(id,name="Luke Musgrave",owner="Chasen Hardy",player="9481"):
    return {"id":id,"league_id":"l1","owner_id":"o1","owner_name":owner,"sleeper_player_id":player,
            "player_name":name,"salary":2,"contract_years_left":1,"contract_total_years":1,
            "player_position":"TE","is_rookie":False,"created_at":"same"}

class ReconciliationTest(unittest.TestCase):
    def test_identical_material_and_typo_only_duplicate(self):
        good,bad=row("good"),row("bad","Like Musgrave")
        self.assertEqual(material_differences(good,bad),{})
        self.assertTrue(is_typo_only_duplicate(good,bad))
    def test_canonical_spelling_selection(self):
        self.assertEqual(select_spelling_canonical([row("bad","Like Musgrave"),row("good")],"Luke Musgrave")["id"],"good")
    def test_conflicting_team_is_not_identical(self):
        self.assertIn("owner_name",material_differences(row("a"),row("b",owner="Nando")))
    def test_roster_evidence_reconciles_when_all_sources_agree(self):
        evidence={"historical_snapshot":"Nando","active_sleeper":"Nando","latest_transaction":"Nando","canonical_current":"Nando"}
        self.assertEqual(reconcile_owner_evidence(evidence),"Nando")
    def test_ambiguous_evidence_blocks(self):
        with self.assertRaises(ValueError): reconcile_owner_evidence({"historical_snapshot":"Nando","active_sleeper":"Connor"})
    def test_migration_has_audit_and_idempotency_keys(self):
        from pathlib import Path
        sql=(Path(__file__).parents[1]/"supabase/migrations/20260801_contract_source_reconciliation.sql").read_text()
        self.assertLess(sql.index("insert into public.contract_source_corrections"),sql.index("delete from public.contracts"))
        self.assertIn("contract-source-correction:luke-musgrave-9481:v1",sql)
        self.assertIn("contract-source-correction:tyler-allgeier-8132:v1",sql)
        self.assertEqual(sql.count("delete from public.contracts"),2)

if __name__=="__main__": unittest.main()
