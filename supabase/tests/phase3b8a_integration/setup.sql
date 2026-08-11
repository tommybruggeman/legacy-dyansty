\set ON_ERROR_STOP on
\ir ../phase3b7c_integration/setup.sql
begin;
set local session_replication_role=replica;
update public.rollover_execution_plans set ordered_operations=ordered_operations||jsonb_build_array(
 jsonb_build_object('operation_id',gen_random_uuid(),'operation_index',15,
  'operation_type','RECONCILE_TARGET_ROSTER_ASSIGNMENTS','handler_version',1,
  'input_schema_version','phase3b8a-target-roster-input-v1','domain','roster',
  'entity_id',:'target_league_season_id','dependency_ids',jsonb_build_array(
   'EXERCISE_VALID_OWNER_OPTIONS','RELEASE_EXPIRED_CONTRACTS','APPLY_COMMISSIONER_HOLDS'),
  'evidence_fingerprint',repeat('f',64),'operation_fingerprint',repeat('f',64)
 )),operation_count=15,
 operation_summary=operation_summary||jsonb_build_object('RECONCILE_TARGET_ROSTER_ASSIGNMENTS',1)
where id=:'plan_id';
commit;
