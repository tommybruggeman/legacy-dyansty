\set ON_ERROR_STOP on
begin;
select set_config('request.jwt.claims',jsonb_build_object('sub',:'actor_id','role','authenticated')::text,true);
select public.approve_rollover_execution_plan_authenticated(jsonb_build_object(
 'rollover_execution_id',:'execution_id','execution_plan_id',:'plan_id','execution_plan_version',1,
 'simulation_id',:'simulation_id','expected_execution_status','authority_ready','expected_plan_status','valid',
 'expected_plan_input_fingerprint',repeat('8',64),'expected_plan_fingerprint',repeat('9',64),
 'expected_operation_count',15,'expected_simulation_result_fingerprint',repeat('7',64),
 'expected_simulation_input_fingerprint',repeat('6',64),'expected_preflight_fingerprint',repeat('2',64),
 'expected_policy_fingerprint',repeat('1',64),'expected_owner_population_fingerprint',repeat('3',64),
 'expected_commissioner_population_fingerprint',repeat('4',64),
 'expected_authority_preparation_fingerprint',repeat('5',64),
 'approval_statement_code','ROLLOVER_EXECUTION_PLAN_APPROVED','approval_statement_version',1,
 'approval_statement','Synthetic disposable Phase 3B.8A approval','idempotency_key',:'approve_key'));
commit;
