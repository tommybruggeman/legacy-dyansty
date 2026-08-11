begin;

create or replace function public.get_rollover_execution_plan_material_authenticated(p_execution_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 actor uuid:=auth.uid(); x public.rollover_executions%rowtype; target public.league_seasons%rowtype;
 membership_count integer; team_count integer; option_count integer;
 mapping_evidence jsonb; decision_evidence jsonb; mapping_fp text; decision_fp text;
begin
 if actor is null then raise exception 'authentication required'; end if;
 select * into x from public.rollover_executions where id=p_execution_id for share;
 if x.id is null then raise exception 'rollover execution not found'; end if;
 select count(*) into membership_count from public.league_memberships m
 where m.league_id=x.league_id and m.user_id=actor and lower(m.role) in('commissioner','host','admin');
 if membership_count<>1 then raise exception 'exactly one canonical commissioner membership required'; end if;
 select * into target from public.league_seasons where league_id=x.league_id and season=x.target_season;
 if target.id is null then raise exception 'target season authority missing'; end if;
 select count(*) into team_count from public.league_teams where league_id=x.league_id;
 select jsonb_build_object(
  'teams',coalesce((select jsonb_agg(jsonb_build_object('id',t.id,'sleeper_roster_id',t.sleeper_roster_id) order by t.id)
                    from public.league_teams t where t.league_id=x.league_id),'[]'::jsonb),
  'memberships',coalesce((select jsonb_agg(jsonb_build_object('id',m.id,'user_id',m.user_id,'role',lower(m.role),'league_team_id',m.league_team_id) order by m.id)
                          from public.league_memberships m where m.league_id=x.league_id and m.league_team_id is not null),'[]'::jsonb),
  'target_mappings',coalesce((select jsonb_agg(jsonb_build_object('id',m.id,'league_team_id',m.league_team_id,'sleeper_roster_id',m.sleeper_roster_id,
                           'mapping_source',m.mapping_source,'mapping_confidence',m.mapping_confidence) order by m.id)
                              from public.season_team_mappings m where m.league_season_id=target.id),'[]'::jsonb)
 ) into mapping_evidence;
 mapping_fp:=public.rollover_material_fingerprint(jsonb_build_object(
  'league_id',x.league_id,'target_season',x.target_season,'expected_team_count',team_count,'evidence',mapping_evidence));
 select count(*) into option_count from public.rollover_owner_decisions where rollover_execution_id=x.id;
 select coalesce(jsonb_agg(jsonb_build_object(
  'id',d.id,'league_team_id',d.league_team_id,'player_id',d.player_id,'agreement_id',d.agreement_id,
  'decision_status',d.decision_status,'owner_choice',d.owner_choice,'planned_outcome',d.planned_outcome,
  'deadline',d.deadline,'locked_at',d.locked_at,'updated_at',d.updated_at,
  'revision_count',(select count(*) from public.rollover_owner_decision_revisions r where r.owner_decision_id=d.id)) order by d.id),'[]'::jsonb)
 into decision_evidence from public.rollover_owner_decisions d where d.rollover_execution_id=x.id;
 decision_fp:=public.rollover_material_fingerprint(jsonb_build_object(
  'execution_id',x.id,'league_id',x.league_id,'source_season',x.source_season,'target_season',x.target_season,
  'notice_timestamp',x.notice_timestamp,'owner_deadline',x.owner_deadline,'decisions',decision_evidence));
 return jsonb_build_object('execution_id',x.id,'league_id',x.league_id,'operation_material',jsonb_build_object(
  'VERIFY_TEAM_ROSTER_MAPPINGS',jsonb_build_object('expected_team_count',team_count,'evidence_fingerprint',mapping_fp),
  'VERIFY_OPTION_WINDOW_CLOSED',jsonb_build_object('expected_eligible_option_count',option_count,
   'expected_notice_timestamp',x.notice_timestamp,'expected_deadline_timestamp',x.owner_deadline,'evidence_fingerprint',decision_fp)));
end $$;

revoke all on function public.get_rollover_execution_plan_material_authenticated(uuid) from public,anon,authenticated,service_role;
grant execute on function public.get_rollover_execution_plan_material_authenticated(uuid) to authenticated;

commit;
