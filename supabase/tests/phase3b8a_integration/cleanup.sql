\set ON_ERROR_STOP on
begin;
set local session_replication_role=replica;
delete from public.season_roster_assignments where assignment_set_id in(
 select id from public.rollover_target_roster_assignment_sets where rollover_execution_id=:'execution_id');
delete from public.rollover_target_roster_assignment_sets where rollover_execution_id=:'execution_id';
commit;
\ir ../phase3b7c_integration/cleanup.sql
