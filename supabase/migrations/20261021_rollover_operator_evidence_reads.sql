-- Read-only evidence exposure for the authenticated production rollover operator UI.
-- No mutation privilege is granted by this migration.
begin;
set local search_path=pg_catalog,public;

do $$
declare table_name text;
begin
 foreach table_name in array array[
  'rollover_post_execution_validation_reports',
  'rollover_target_roster_assignment_sets','rollover_taxi_unlock_sets',
  'rollover_draft_inventory_generations','prepared_rookie_eligibility_sets',
  'prepared_target_standings_sets','prepared_target_matchup_sets',
  'prepared_target_playoff_structures','prepared_team_cap_sets',
  'prepared_free_agent_eligibility_sets','prepared_expiring_contract_sets',
  'season_cache_invalidation_manifests','rollover_executed_unpublished_finalizations',
  'rollover_target_season_authority_publications','rollover_target_cap_authority_publications'
 ] loop
  execute format('grant select on public.%I to authenticated',table_name);
  execute format('drop policy if exists rollover_operator_commissioner_read on public.%I',table_name);
  execute format($policy$
   create policy rollover_operator_commissioner_read on public.%I for select to authenticated
   using(exists(select 1 from public.league_memberships membership
    where membership.league_id=%I.league_id and membership.user_id=auth.uid()
     and membership.role in('commissioner','host','admin')))
  $policy$,table_name,table_name);
 end loop;
end$$;

grant select on public.rollover_post_execution_validation_checks to authenticated;
drop policy if exists rollover_operator_commissioner_read on public.rollover_post_execution_validation_checks;
create policy rollover_operator_commissioner_read
on public.rollover_post_execution_validation_checks for select to authenticated
using(exists(
 select 1 from public.rollover_post_execution_validation_reports report
 join public.league_memberships membership on membership.league_id=report.league_id
 where report.id=rollover_post_execution_validation_checks.report_id
  and membership.user_id=auth.uid() and membership.role in('commissioner','host','admin')
));

commit;
