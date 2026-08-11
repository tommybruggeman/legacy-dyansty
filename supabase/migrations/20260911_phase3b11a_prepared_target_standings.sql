begin;

alter table public.rollover_execution_handler_registry drop constraint rollover_execution_handler_registry_mutation_class_check;
alter table public.rollover_execution_handler_registry add constraint rollover_execution_handler_registry_mutation_class_check check(mutation_class in(
 'read_only','contract_domain','roster_domain','taxi_domain','taxi_eligibility_domain','ir_domain','draft_inventory_domain','rookie_authority_domain','rookie_eligibility_domain','dead_cap_domain','team_cap_domain','free_agent_domain','expiring_contract_domain','standings_domain'));
insert into public.rollover_execution_handler_registry(operation_code,operation_order,handler_version,input_schema_version,result_schema_version,execution_owner,mutation_class,metadata)
values('INITIALIZE_TARGET_STANDINGS',26,1,'phase3b11a-standings-input-v1','phase3b11a-standings-result-v1','execution','standings_domain',jsonb_build_object('phase','3B.11A','initial_state','all_zero','publication',false,'matchups',false,'playoffs',false));

do $$declare d text;sig regprocedure;begin foreach sig in array array[
 'public.guard_contract_write_during_rollover()'::regprocedure,
 'public.guard_season_roster_assignment_insert_phase3b8a()'::regprocedure,
 'public.guard_phase3b9a_draft_inventory()'::regprocedure,
 'public.guard_phase3b9b_rookie_authority()'::regprocedure,
 'public.guard_phase3b9c_rookie_eligibility()'::regprocedure]
 loop select pg_get_functiondef(sig) into d;d:=replace(d,'''phase3b10d-v1'')','''phase3b10d-v1'',''phase3b11a-v1'')');execute d;end loop;end$$;

create table public.prepared_target_standings_sets(
 id uuid primary key default gen_random_uuid(),rollover_execution_id uuid not null unique references public.rollover_executions(id),
 league_id uuid not null references public.leagues(id),target_season_id uuid not null references public.league_seasons(id),
 source_snapshot_hash text not null check(source_snapshot_hash~'^[0-9a-f]{64}$'),team_population_hash text not null check(team_population_hash~'^[0-9a-f]{64}$'),
 expected_team_count integer not null check(expected_team_count>0),aggregate_standings_hash text not null check(aggregate_standings_hash~'^[0-9a-f]{64}$'),
 status text not null check(status='prepared'),published_at timestamptz,schema_version text not null check(schema_version='phase3b11a-standings-v1'),
 created_at timestamptz not null default clock_timestamp(),unique(league_id,target_season_id),check(published_at is null));

create table public.prepared_target_standings(
 id uuid primary key default gen_random_uuid(),standings_set_id uuid not null references public.prepared_target_standings_sets(id),
 league_id uuid not null references public.leagues(id),target_season_id uuid not null references public.league_seasons(id),
 league_team_id uuid not null references public.league_teams(id),wins integer not null default 0 check(wins=0),losses integer not null default 0 check(losses=0),ties integer not null default 0 check(ties=0),
 points_for numeric not null default 0 check(points_for=0),points_against numeric not null default 0 check(points_against=0),standing_points numeric not null default 0 check(standing_points=0),
 regular_season_rank integer,playoff_seed integer,final_finish integer,division_rank integer,waiver_order integer,playoff_status text,streak text,
 source_snapshot_hash text not null check(source_snapshot_hash~'^[0-9a-f]{64}$'),team_population_hash text not null check(team_population_hash~'^[0-9a-f]{64}$'),
 deterministic_row_fingerprint text not null check(deterministic_row_fingerprint~'^[0-9a-f]{64}$'),created_at timestamptz not null default clock_timestamp(),
 unique(standings_set_id,league_team_id),check(regular_season_rank is null and playoff_seed is null and final_finish is null and division_rank is null and waiver_order is null and playoff_status is null and streak is null));
create index prepared_target_standings_order_idx on public.prepared_target_standings(league_id,target_season_id,league_team_id);

alter table public.prepared_target_standings_sets enable row level security;alter table public.prepared_target_standings enable row level security;
revoke all on public.prepared_target_standings_sets,public.prepared_target_standings from public,anon,authenticated;
grant select,insert on public.prepared_target_standings_sets,public.prepared_target_standings to service_role;
create policy prepared_target_standings_set_commissioner_read on public.prepared_target_standings_sets for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=prepared_target_standings_sets.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy prepared_target_standings_row_commissioner_read on public.prepared_target_standings for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=prepared_target_standings.league_id and m.user_id=auth.uid() and m.role='commissioner'));

create or replace function public.guard_phase3b11a_prepared_standings() returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=coalesce(new.league_id,old.league_id);begin if tg_op in('UPDATE','DELETE') then raise exception 'prepared_target_standings_immutable';end if;if public.has_active_rollover_cutover_lock(lid) and coalesce(current_setting('app.rollover_typed_execution',true),'')<>'phase3b11a-v1' then raise exception 'rollover_cutover_standings_writes_blocked';end if;return new;end$$;
create trigger prepared_target_standings_sets_guard before insert or update or delete on public.prepared_target_standings_sets for each row execute function public.guard_phase3b11a_prepared_standings();
create trigger prepared_target_standings_rows_guard before insert or update or delete on public.prepared_target_standings for each row execute function public.guard_phase3b11a_prepared_standings();

create or replace function public.write_prepared_target_standings_phase3b11a_private(p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;target public.league_seasons%rowtype;snap public.rollover_execution_input_snapshots%rowtype;existing public.prepared_target_standings_sets%rowtype;t public.league_teams%rowtype;
 teamhash text;sethash text;rowhash text;material jsonb:='[]';teamcount int:=0;written int:=0;
begin
 if p_actor is null then perform public.raise_phase3b6c1_failure('standings_authenticated_actor_required','{}');end if;
 select * into x from public.rollover_executions where id=p_rollover_execution_id;
 select * into target from public.league_seasons where league_id=x.league_id and season=x.target_season for share;
 if target.id is null then perform public.raise_phase3b6c1_failure('standings_target_season_missing','{}');end if;
 if target.league_id<>x.league_id or target.is_active or target.status<>'scheduled' then perform public.raise_phase3b6c1_failure('standings_target_season_invalid','{}');end if;
 if not exists(select 1 from public.rollover_execution_locks l where l.rollover_execution_id=x.id and l.execution_plan_id=p_execution_plan_id and l.approval_id=p_approval_id and l.status='active' and l.lock_type='cutover' and l.lock_scope='rollover_global' for update) then perform public.raise_phase3b6c1_failure('standings_cutover_lock_missing','{}');end if;
 select * into snap from public.rollover_execution_input_snapshots where rollover_execution_id=x.id and execution_plan_id=p_execution_plan_id and approval_id=p_approval_id for share;
 if snap.id is null or snap.league_id<>x.league_id then perform public.raise_phase3b6c1_failure('standings_source_hash_mismatch','{}');end if;
 perform 1 from public.league_teams where league_id=x.league_id order by id for share;perform 1 from public.prepared_target_standings_sets where rollover_execution_id=x.id for update;
 if exists(select 1 from public.season_standings where league_season_id=target.id) then perform public.raise_phase3b6c1_failure('standings_target_already_initialized','{}');end if;
 select count(*) into teamcount from public.league_teams where league_id=x.league_id;if teamcount<=0 then perform public.raise_phase3b6c1_failure('standings_team_population_incomplete','{}');end if;
 teamhash:=public.rollover_material_fingerprint((select jsonb_agg(id order by id) from public.league_teams where league_id=x.league_id));
 for t in select * from public.league_teams where league_id=x.league_id order by id loop
  rowhash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b11a-standings-row-v1','execution',x.id,'league',x.league_id,'target_season',target.id,'team',t.id,'wins',0,'losses',0,'ties',0,'points_for',0,'points_against',0,'standing_points',0,'rank',null,'playoff_seed',null,'final_finish',null,'division_rank',null,'waiver_order',null,'playoff_status',null,'streak',null,'snapshot',snap.aggregate_snapshot_fingerprint,'team_population_hash',teamhash));
  material:=material||jsonb_build_array(jsonb_build_object('team_id',t.id,'row_hash',rowhash));end loop;
 sethash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b11a-standings-set-v1','execution',x.id,'target_season',target.id,'team_population_hash',teamhash,'rows',material));
 select * into existing from public.prepared_target_standings_sets where league_id=x.league_id and target_season_id=target.id for update;
 if found then if existing.rollover_execution_id<>x.id or existing.team_population_hash<>teamhash or existing.aggregate_standings_hash<>sethash or existing.expected_team_count<>teamcount then perform public.raise_phase3b6c1_failure('standings_set_conflict','{}');end if;if(select count(*) from public.prepared_target_standings where standings_set_id=existing.id)<>teamcount then perform public.raise_phase3b6c1_failure('standings_set_hash_mismatch','{}');end if;return jsonb_build_object('canonical_team_count',teamcount,'standings_rows_written',0,'compatible_replay_count',1,'zero_wins_count',teamcount,'zero_losses_count',teamcount,'zero_ties_count',teamcount,'zero_points_count',teamcount,'ranked_team_count',0,'seeded_team_count',0,'mutation_count',0,'postcondition_count',12,'aggregate_standings_hash',sethash,'validation_codes',jsonb_build_array('target_scheduled','canonical_teams','all_zero','no_rank','no_seed','no_matchups','no_playoffs','unpublished','cutover_lock'));
 end if;
 perform set_config('app.rollover_typed_execution','phase3b11a-v1',true);
 insert into public.prepared_target_standings_sets(rollover_execution_id,league_id,target_season_id,source_snapshot_hash,team_population_hash,expected_team_count,aggregate_standings_hash,status,schema_version)
 values(x.id,x.league_id,target.id,snap.aggregate_snapshot_fingerprint,teamhash,teamcount,sethash,'prepared','phase3b11a-standings-v1') returning * into existing;
 for t in select * from public.league_teams where league_id=x.league_id order by id loop
  rowhash:=public.rollover_material_fingerprint(jsonb_build_object('schema','phase3b11a-standings-row-v1','execution',x.id,'league',x.league_id,'target_season',target.id,'team',t.id,'wins',0,'losses',0,'ties',0,'points_for',0,'points_against',0,'standing_points',0,'rank',null,'playoff_seed',null,'final_finish',null,'division_rank',null,'waiver_order',null,'playoff_status',null,'streak',null,'snapshot',snap.aggregate_snapshot_fingerprint,'team_population_hash',teamhash));
  insert into public.prepared_target_standings(standings_set_id,league_id,target_season_id,league_team_id,source_snapshot_hash,team_population_hash,deterministic_row_fingerprint)
  values(existing.id,x.league_id,target.id,t.id,snap.aggregate_snapshot_fingerprint,teamhash,rowhash);written:=written+1;end loop;
 if written<>teamcount then perform public.raise_phase3b6c1_failure('standings_population_incomplete','{}');end if;
 return jsonb_build_object('canonical_team_count',teamcount,'standings_rows_written',written,'compatible_replay_count',0,'zero_wins_count',teamcount,'zero_losses_count',teamcount,'zero_ties_count',teamcount,'zero_points_count',teamcount,'ranked_team_count',0,'seeded_team_count',0,'mutation_count',written+1,'postcondition_count',12,'aggregate_standings_hash',sethash,'validation_codes',jsonb_build_array('target_scheduled','canonical_teams','all_zero','no_rank','no_seed','no_matchups','no_playoffs','unpublished','cutover_lock'));
end$$;

create or replace function public.execute_rollover_typed_handler_phase3b11a_private(p_operation jsonb,p_rollover_execution_id uuid,p_execution_plan_id uuid,p_approval_id uuid,p_actor uuid) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$declare code text:=p_operation->>'operation_type';result jsonb;begin if code<>'INITIALIZE_TARGET_STANDINGS' then return public.execute_rollover_typed_handler_phase3b10d_private(p_operation,p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);end if;if(p_operation->>'operation_index')::int<>26 then perform public.raise_phase3b6c1_failure('unsupported_handler_version','{}');end if;result:=public.write_prepared_target_standings_phase3b11a_private(p_rollover_execution_id,p_execution_plan_id,p_approval_id,p_actor);return jsonb_build_object('operation_code',code,'handler_version',1,'result',result||jsonb_build_object('operation_mutation_count',(result->>'mutation_count')::int));end$$;
do $$declare d text;begin select pg_get_functiondef('public.execute_rollover_plan_phase3b10d_private(jsonb,uuid)'::regprocedure) into d;d:=replace(d,'execute_rollover_plan_phase3b10d_private','execute_rollover_plan_phase3b11a_private');d:=replace(d,'execute_rollover_typed_handler_phase3b10d_private','execute_rollover_typed_handler_phase3b11a_private');d:=replace(d,'''RECONCILE_EXPIRING_CONTRACT_ELIGIBILITY'')','''RECONCILE_EXPIRING_CONTRACT_ELIGIBILITY'',''INITIALIZE_TARGET_STANDINGS'')');d:=replace(d,'Phase 3B.10D','Phase 3B.11A');d:=replace(d,'phase3b10d-v1','phase3b11a-v1');execute d;end$$;
create or replace function public.execute_rollover_plan_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$declare actor uuid:=public.require_authenticated_user();x public.rollover_executions%rowtype;begin if p_request?'actor_user_id' or p_request?'executed_by' or p_request?'team_ids' or p_request?'standings' or p_request?'wins' or p_request?'rank' or p_request?'seed' then raise exception 'actor or standings material spoofing forbidden';end if;select * into x from public.rollover_executions where id=(p_request->>'rollover_execution_id')::uuid;if x.id is null then raise exception 'execution not found';end if;perform public.require_commissioner_authority(x.league_id);return public.execute_rollover_plan_phase3b11a_private(p_request,actor);end$$;
revoke all on function public.write_prepared_target_standings_phase3b11a_private(uuid,uuid,uuid,uuid),public.execute_rollover_typed_handler_phase3b11a_private(jsonb,uuid,uuid,uuid,uuid),public.execute_rollover_plan_phase3b11a_private(jsonb,uuid),public.guard_phase3b11a_prepared_standings() from public,anon,authenticated,service_role;
revoke all on function public.execute_rollover_plan_authenticated(jsonb) from public,anon,authenticated,service_role;grant execute on function public.execute_rollover_plan_authenticated(jsonb) to authenticated;
commit;
