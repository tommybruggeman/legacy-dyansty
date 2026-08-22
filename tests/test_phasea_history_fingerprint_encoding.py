from pathlib import Path
import unittest

from season_engine.history.fingerprints import (
    canonical_team_set_fingerprint, mapping_set_fingerprint,
    source_roster_set_fingerprint, standings_set_fingerprint,
)


VECTORS = {
    1: ("81c0de35b42208a510c2c34b1d371037cd409c9ba0300c7d36608ab42d57e388", "15c40147aa7f1f0acaa11c2f0e1cd534889ee0d19a6d2abc0165ce9e106710c5", "81c0de35b42208a510c2c34b1d371037cd409c9ba0300c7d36608ab42d57e388", "4571c13bd9b3cbc799dd4930457aa0f235615ddc3ccabe5d50b3f214a84bdd55"),
    10: ("7c6371c6ca4e27c17dbce29b25b235ededf768dadbfa8cfbe2afd904c59bd271", "2e72c261be2b3e2c2ad060ee5eb0e2b7b150322f57ec7a98ed18a399a27923b9", "7c6371c6ca4e27c17dbce29b25b235ededf768dadbfa8cfbe2afd904c59bd271", "aef4c7fe5e751465a1191feeaf610e6a5b14c7fbe5fc832921e6bff54e271864"),
    32: ("d2c571d000cab98bf24e0b4cdcb3ca6a8d5383fb2587d5f6cdbadd0e38d2b8fd", "59edad9a0876c8dc4347f5e76f281fee179ba79799da805dd235fe29eed40046", "d2c571d000cab98bf24e0b4cdcb3ca6a8d5383fb2587d5f6cdbadd0e38d2b8fd", "f3dbb23019c20f8ed25adf001905d990c29f9e856f2d53899c358af693571411"),
    100: ("eb82f010324ef11e50b45e9cc0168f888773a43127928c0f63ab13adfd818faf", "47160d6b2ecae7ef062ba394518ab634052118cfd071d01c47a1582ad71c511e", "eb82f010324ef11e50b45e9cc0168f888773a43127928c0f63ab13adfd818faf", "1cc2f479733f4d85f24526eea18fbf0f5e76fdc17d2bdfa5bea741b90222da9e"),
    2000: ("f911925ad14d55c8eaf02ad2e54a0ea41859f205509c400d275c2d0cab4a8116", "e982f418a70288a6ae74664abe21219c5f133170d9f30e9f092685a73fe87cda", "f911925ad14d55c8eaf02ad2e54a0ea41859f205509c400d275c2d0cab4a8116", "38a11681d31c285311ac58abb696725ef49dd488178e9fb9749e5c2d12b34bd0"),
}


class PhaseAFingerprintEncodingTest(unittest.TestCase):
    def test_python_matches_fixed_cross_language_vectors(self):
        for count, expected in VECTORS.items():
            teams=[{"id":f"00000000-0000-0000-0000-{i:012d}","sleeper_roster_id":i,"sleeper_user_id":f"user-{i}"} for i in range(1,count+1)]
            source=[{"roster_id":i,"owner_id":f"user-{i}"} for i in range(1,count+1)]
            mappings=[{"league_team_id":row["id"],"sleeper_roster_id":row["sleeper_roster_id"],"sleeper_user_id":row["sleeper_user_id"]} for row in teams]
            standings=[{"league_team_id":row["id"]} for row in teams]
            actual=(canonical_team_set_fingerprint(reversed(teams)),source_roster_set_fingerprint(reversed(source)),
                    mapping_set_fingerprint(reversed(mappings)),standings_set_fingerprint(reversed(standings)))
            self.assertEqual(actual,expected)

    def test_migration_is_forward_only_and_private(self):
        sql=(Path(__file__).resolve().parents[1]/"supabase/migrations/20261008_phaseA_history_fingerprint_enforcement.sql").read_text().lower()
        self.assertIn("rename to capture_pre_rollover_history_phasea_set_validated_private",sql)
        self.assertIn("security definer set search_path=pg_catalog,public",sql)
        self.assertIn("grant execute on function public.capture_pre_rollover_history(jsonb) to service_role",sql)
        self.assertNotIn("grant execute on function public.phasea_history_",sql)

    def test_rollback_tests_cover_each_rejection(self):
        root=Path(__file__).resolve().parents[1]
        sql=(root/"supabase/tests/20261008_phaseA_history_fingerprint_enforcement_test.sql").read_text().lower()
        self.assertTrue(sql.rstrip().endswith("rollback;"));self.assertNotIn("commit;",sql)
        for name in ("canonical_team_set_fingerprint","source_roster_set_fingerprint","mapping_set_fingerprint","standings_set_fingerprint"):
            self.assertIn(name,sql)
        for marker in ("fingerprint_missing","fingerprint_malformed","fingerprint_mismatch","partial capture state"):
            self.assertIn(marker,sql)

    def test_cross_language_database_vectors_are_rollback_only(self):
        sql=(Path(__file__).resolve().parents[1]/"supabase/tests/20261008_phaseA_history_cross_language_vectors_test.sql").read_text().lower()
        self.assertTrue(sql.rstrip().endswith("rollback;"));self.assertNotIn("commit;",sql)
        for count in (1,10,32,100,2000): self.assertIn(str(count),sql)


if __name__=="__main__": unittest.main()
