from pathlib import Path
import re
import unittest

from season_engine.decision_population_fingerprints import (
    compact_json, commissioner_case_fingerprint, commissioner_case_material,
    owner_case_fingerprint, owner_case_material, population_fingerprint,
)

ROOT=Path(__file__).resolve().parents[1]
MIGRATION=(ROOT/'supabase/migrations/20261010_phaseb_population_fingerprint_parity.sql').read_text()
SQL_VECTORS=(ROOT/'supabase/tests/20261010_phaseb_population_fingerprint_vectors_test.sql').read_text()
CASE_MIGRATION=(ROOT/'supabase/migrations/20261011_phaseb_case_fingerprint_parity.sql').read_text()
CASE_VECTORS=(ROOT/'supabase/tests/20261011_phaseb_case_fingerprint_vectors_test.sql').read_text()
INTEGRITY_MIGRATION=(ROOT/'supabase/migrations/20261012_phaseb_cross_league_case_integrity.sql').read_text()
IDENTIFIER_MIGRATION=(ROOT/'supabase/migrations/20261013_phaseb_plpgsql_identifier_disambiguation.sql').read_text()
IDENTIFIER_TEST=(ROOT/'supabase/tests/20261013_phaseb_plpgsql_identifier_disambiguation_test.sql').read_text()
RUNTIME_MIGRATION=(ROOT/'supabase/migrations/20261014_phaseb_runtime_hardening.sql').read_text()
RUNTIME_TEST=(ROOT/'supabase/tests/20261014_phaseb_runtime_hardening_test.sql').read_text()
COMPATIBILITY_MIGRATION=(ROOT/'supabase/migrations/20261015_phaseb_runtime_schema_compatibility.sql').read_text()
COMPATIBILITY_TEST=(ROOT/'supabase/tests/20261015_phaseb_runtime_schema_compatibility_test.sql').read_text()
COMPATIBILITY_VERIFICATION=(ROOT/'supabase/verification/verify_phaseb_runtime_schema_compatibility.sql').read_text()
RUNNER=(ROOT/'tests/phase_b_decision_population_integration/run_phase_b_decision_population_certification.py').read_text()

OWNER={'classification':'ROSTERED_EXPIRED_POLICY_UNDEFINED','league_id':'00000000-0000-0000-0000-000000000001','source_season':2025,'target_season':2026,'agreement_id':'00000000-0000-0000-0000-000000000002','player_id':'player-α','league_team_id':'00000000-0000-0000-0000-000000000003','agreement_status':'expired','roster_designation':'rostered','sleeper_player_id':'player-α','source_salary':'5','source_contract_years':0}
COMMISSIONER={'review_type':'active_off_roster_liability','agreement_id':'00000000-0000-0000-0000-000000000002','player_id':'player-β','league_team_id':'00000000-0000-0000-0000-000000000003','source_identity':None,'agreement_status':'active','roster_status':'unrostered','source_salary':'3.0','source_contract_years':1}

class PhaseBFingerprintParityTests(unittest.TestCase):
 def test_fixed_case_vectors_and_normalization(self):
  self.assertEqual(owner_case_material(OWNER),['phaseb-owner-case-v3','ROSTERED_EXPIRED_POLICY_UNDEFINED','00000000-0000-0000-0000-000000000001',2025,2026,'00000000-0000-0000-0000-000000000002','player-α','00000000-0000-0000-0000-000000000003','expired','rostered','player-α','5.00',0])
  self.assertEqual(owner_case_fingerprint(OWNER),'c3ca59e85daccdacf224ebedeb552c03d740b321bad51c8a70abf8dc2ab70d0c')
  self.assertEqual(commissioner_case_material(COMMISSIONER),['phaseb-commissioner-case-v3','active_off_roster_liability','00000000-0000-0000-0000-000000000002','player-β','00000000-0000-0000-0000-000000000003',None,'active','unrostered','3.00',1])
  self.assertEqual(commissioner_case_fingerprint(COMMISSIONER),'b6f175571a1d7b797e0e23bf88aa9f73e5584293648c49b155b23ce9ff23cc6f')
 def test_owner_population_zero_one_108_large_and_shuffle(self):
  fp=owner_case_fingerprint(OWNER);expected={0:'8923d167bdc818ec198f566e6e2ee9662f43a1a2f6219231bae187d6ca79bac2',1:'567840418d18d8ca2d7e49161a2394cded2fe435f25617309da2e8c1b952682a',108:'4ee22a795adea6d5498d21bbc4ac2b2ceafb56b1b4a7babf1a26767f49244dbb',120:'db982c434d450c8a0e4640e74f45b2431a4d647723a73de33cc2dcba00dd7947'}
  for n,value in expected.items():
   rows=[{'case_key':f'{i:04d}','evidence_fingerprint':fp} for i in range(n)]
   self.assertEqual(population_fingerprint('owner',rows),value);self.assertEqual(population_fingerprint('owner',reversed(rows)),value)
 def test_commissioner_population_vectors_proactively(self):
  fp=commissioner_case_fingerprint(COMMISSIONER);expected={0:'17b1e4ce49fcc3354c96911b3396cd77561786d9639516a1989c2e32ffdb3d50',1:'f2137bae9356849cc1c204f20fe2617312d2e8bd16b96a35fc4f86db281ca0bd',13:'927d9cd3a10590054b9f8d9b8f8e0bbcc74dec58f0283a38e1c2eaaf979769f2',25:'d73eca80537d3b9718f1835dbdc559b6d4fef253825fa3d4d666cbf5923e0bdf'}
  for n,value in expected.items():
   rows=[{'case_key':f'{i:04d}','evidence_fingerprint':fp} for i in range(n)]
   self.assertEqual(population_fingerprint('commissioner',rows),value);self.assertEqual(population_fingerprint('commissioner',reversed(rows)),value)
 def test_owner_case_salary_null_and_numeric_vectors(self):
  base={**OWNER,'source_contract_years':12}
  expected={None:'ef7d360fa0664fd60ce087ab4ffcf32824fc1d8429bb25aacd6c41b81be91c8e',0:'a5226667a3234114fe0381959fbcb85efb3582d2a3449336ff10b6fb9cec9c92',1:'0321b91118a1adad5321a131b76505b61fcbdbb73046b268ffceb9cd35247cfe','1.5':'183ca0da0ced212686f3137749a02c81e84179af19da7c2ff4fa2f4436f55cfa','10.00':'c18867fd3d169eda59d0a337674f2e95585d42f82e6febe258c76be2e48006ff'}
  for salary,fingerprint in expected.items():
   case={**base,'source_salary':salary}
   self.assertEqual(owner_case_fingerprint(case),fingerprint)
   self.assertEqual(owner_case_fingerprint(dict(reversed(tuple(case.items())))),fingerprint)
  self.assertIn('"1.50",12]',compact_json(owner_case_material({**base,'source_salary':'1.5'})))
 def test_commissioner_base_escalation_conflict_and_null_vectors(self):
  vectors=(
   (COMMISSIONER,'b6f175571a1d7b797e0e23bf88aa9f73e5584293648c49b155b23ce9ff23cc6f'),
   ({**COMMISSIONER,'review_type':'owner_escalation','source_identity':'00000000-0000-0000-0000-000000000004'},'69583a9a5d078c90d371f155b574fac6029e0bcdf6c7b2e89caff24ec07f2348'),
   ({**COMMISSIONER,'review_type':'identity_conflict','source_identity':'00000000-0000-0000-0000-000000000005'},'f24e2ca2ebe28b728b5875e496d062ba8817a065572a7cc77ec4f1ce1a44fe2d'),
   ({**COMMISSIONER,'review_type':'identity_conflict','source_identity':'00000000-0000-0000-0000-000000000006'},'e9e1d804d68a1ed5905a8158934f0726d4261dfa3bb191d57b4dcc2077fc4882'),
   ({**COMMISSIONER,'agreement_id':None,'league_team_id':None,'source_identity':None,'agreement_status':None,'source_salary':None},'9e2b863c8f16a558673672ff8b82ef425760d85d4e8c42b951402e4344a0c480'))
  for case,fingerprint in vectors:
   self.assertEqual(commissioner_case_fingerprint(case),fingerprint)
   self.assertEqual(commissioner_case_fingerprint(dict(reversed(tuple(case.items())))),fingerprint)
  self.assertNotEqual(vectors[2][1],vectors[3][1])
 def test_forward_migration_is_private_atomic_and_positional(self):
  self.assertTrue(MIGRATION.strip().lower().endswith('commit;'));self.assertIn('\nbegin;',MIGRATION.lower())
  self.assertIn('phaseb-owner-case-v3',MIGRATION);self.assertIn('phaseb-commissioner-case-v3',MIGRATION);self.assertIn("population-v3",MIGRATION)
  self.assertIn('revoke all on function public.phaseb_sha256_private',MIGRATION)
  self.assertIn('BEGIN',SQL_VECTORS.upper());self.assertIn('ROLLBACK',SQL_VECTORS.upper())
 def test_sql_vectors_have_complete_typed_case_and_block_structure(self):
  sql=SQL_VECTORS.lower()
  self.assertIn('expected_fp text;',sql)
  self.assertEqual(sql.count('expected_fp := case n'),2)
  self.assertEqual(sql.count('else null::text\n        end;'),2)
  self.assertNotIn('if fp<>case',sql.replace(' ',''))
  self.assertIn('array[0, 1, 108, 120]',sql)
  self.assertIn('array[0, 1, 13, 25]',sql)
  self.assertEqual(sql.count('order by g desc'),2)
  self.assertIn('owner reversed population vector',sql)
  self.assertIn('commissioner reversed population vector',sql)
  self.assertRegex(sql,r'end;\s*\$\$;\s*\n\s*rollback;\s*$')
 def test_case_correction_is_forward_atomic_private_and_shared(self):
  sql=CASE_MIGRATION.lower()
  self.assertTrue(sql.strip().endswith('commit;'))
  self.assertIn('\nbegin;',sql)
  for helper in ('phaseb_owner_case_material_v3_private','phaseb_commissioner_case_material_v3_private','phaseb_owner_case_fingerprint_v3_private','phaseb_commissioner_case_fingerprint_v3_private'):
   self.assertIn(helper,sql);self.assertIn(helper,CASE_VECTORS)
  self.assertGreaterEqual(sql.count('security definer'),6)
  self.assertIn('from public,anon,authenticated,service_role',sql)
 def test_identifier_repair_uses_explicit_noncolliding_aliases(self):
  sql=IDENTIFIER_MIGRATION.lower()
  self.assertIn('\nbegin;',sql);self.assertTrue(sql.strip().endswith('commit;'))
  for marker in ('supplied_case_doc','execution_row','expected_set','actual_set',
                 'supplied_rows(supplied_case_value)','expected_rows(expected_case_doc)',
                 'actual_rows(actual_case_doc)','<<assert_population>>','end assert_population;'):
   self.assertIn(marker,sql)
  for ambiguous in ('jsonb_array_elements(assert_population.p_supplied)c',
                    'jsonb_array_elements(assert_population.p_supplied) c'):
   self.assertNotIn(ambiguous,sql)
  self.assertIn('security definer set search_path=pg_catalog,public',sql)
  self.assertIn('phaseb_owner_cross_league_or_identity_mismatch',sql)
  self.assertIn('phaseb_commissioner_cross_league_or_identity_mismatch',sql)
  self.assertIn('phaseb_%_case_fingerprint_mismatch',sql)
  self.assertIn('phaseb_%_population_set_mismatch',sql)
  self.assertIn('begin;',IDENTIFIER_TEST.lower());self.assertIn('rollback;',IDENTIFIER_TEST.lower())
 def test_runtime_hardening_is_boring_unambiguous_and_fail_closed(self):
  sql=RUNTIME_MIGRATION.lower()
  self.assertIn('\nbegin;',sql);self.assertTrue(sql.strip().endswith('commit;'))
  self.assertNotIn('assert_population.p_',sql)
  self.assertNotRegex(sql,r'jsonb_array_elements\([^\n]+\)\s+[cerx]\b')
  self.assertIn('#variable_conflict error',sql)
  for marker in ('p_supplied is null','p_kind is null','phaseb_duplicate_expected_%_case',
                 'phaseb_owner_cross_league_or_identity_mismatch','phaseb_commissioner_source_identity_cross_league',
                 'phaseb_%_case_fingerprint_mismatch','phaseb_%_population_set_mismatch'):
   self.assertIn(marker,sql)
  # The authenticated lifecycle wrapper still uses the legacy operation-request
  # fingerprint only for idempotency, never for Phase B v3 case authority.
  case_assertion=sql.split('create or replace function public.phaseb_assert_population_private',1)[1].split('create or replace function public.phaseb_assert_frozen_populations_private',1)[0]
  self.assertNotIn('rollover_material_fingerprint',case_assertion)
  self.assertIn('security definer set search_path=pg_catalog,public',sql)
  self.assertIn('from public,anon,authenticated,service_role',sql)
  self.assertIn('phaseb_commissioner_conflict_source_missing',sql)
  self.assertIn('grant execute on function public.initialize_rollover_commissioner_reviews_authenticated(jsonb) to authenticated',sql)
 def test_runtime_preflight_invokes_real_zero_one_large_and_negative_branches(self):
  sql=RUNTIME_TEST.lower()
  self.assertTrue(sql.strip().startswith('begin;'));self.assertTrue(sql.strip().endswith('rollback;'))
  for marker in ('owner zero runtime','owner one runtime','owner large runtime','owner missing accepted',
                 'owner duplicate accepted','owner stale accepted','owner foreign accepted',
                 'commissioner zero runtime','commissioner one runtime','commissioner large runtime',
                 'commissioner stale accepted','commissioner foreign accepted','commissioner escalation runtime',
                 'commissioner distinct conflict runtime','duplicate phaseb_case_key accepted',
                 'invalid transition accepted','authority_initializing'):
   self.assertIn(marker,sql)
  self.assertGreaterEqual(sql.count('phaseb_assert_population_private'),10)
  self.assertNotIn('rollover_material_fingerprint',RUNNER)
  self.assertIn('case_material_v3_private',RUNNER)
  self.assertIn('case_fingerprint_v3_private',RUNNER)
 def test_runtime_schema_compatibility_uses_canonical_plan_approval_surfaces(self):
  sql=COMPATIBILITY_MIGRATION.lower()
  self.assertTrue(sql.strip().startswith('begin;'));self.assertTrue(sql.strip().endswith('commit;'))
  self.assertNotIn('p.status',sql)
  for marker in ("plan_status='approved_for_execution'","approved_for_execution=true",
                 "approval_status='approved'",'phaseb_commissioner_review_plan_approved_private'):
   self.assertIn(marker,sql)
  self.assertEqual(sql.count('create or replace function'),3)
  migration_time_sql=re.sub(r'\$\$.*?\$\$','',sql,flags=re.S)
  self.assertNotRegex(migration_time_sql,r'(?m)^\s*(insert\s+into|update|delete\s+from)\s+public\.')
  self.assertIn('security definer',sql)
  self.assertIn('set search_path=pg_catalog,public',sql)
  self.assertIn('grant execute on function public.supersede_rollover_commissioner_review_authenticated(jsonb)',sql)
  self.assertTrue(COMPATIBILITY_TEST.strip().lower().startswith('begin;'))
  self.assertTrue(COMPATIBILITY_TEST.strip().lower().endswith('rollback;'))
  self.assertIn('phaseb_commissioner_review_plan_approved_private(v_execution_id)',COMPATIBILITY_TEST)
  self.assertIn('begin read only;',COMPATIBILITY_VERIFICATION.lower())
  self.assertIn('rollback;',COMPATIBILITY_VERIFICATION.lower())
 def test_cross_league_integrity_is_relational_fail_closed_and_atomic(self):
  sql=INTEGRITY_MIGRATION.lower()
  self.assertIn('\nbegin;',sql);self.assertTrue(sql.strip().endswith('commit;'))
  for marker in ('phaseb_%_execution_boundary_mismatch','phaseb_owner_cross_league_or_identity_mismatch',
                 'phaseb_commissioner_cross_league_or_identity_mismatch','phaseb_commissioner_team_cross_league',
                 'phaseb_commissioner_source_identity_cross_league','phaseb_%_case_fingerprint_mismatch',
                 'phaseb_%_population_set_mismatch'):
   self.assertIn(marker,sql)
  for relation in ('contract_agreements','league_teams','league_seasons','season_roster_assignments'):
   self.assertIn(relation,sql)
  self.assertIn('security definer set search_path=pg_catalog,public',sql)
  self.assertIn('from public,anon,authenticated,service_role',sql)

if __name__=='__main__':unittest.main()
