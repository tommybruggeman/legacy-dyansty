begin;

-- One-time bridge for the live 2026 league whose roster is still authoritative in
-- public.contracts.  Validation is completed before the first canonical write.
create or replace function public.bootstrap_legacy_contracts_private(
 p_league_id uuid,p_expected_source_count integer)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare
 active_year integer; active_id uuid; source_count integer; accepted_count integer;
 source_fp text; execution_key text; existing public.contract_backfill_executions%rowtype;
 agreement_count integer; season_count integer; event_count integer;
begin
 if p_league_id is null or p_expected_source_count<1 then raise exception 'legacy bootstrap identity and positive expected count required';end if;
 perform pg_advisory_xact_lock(hashtextextended('legacy-contract-bootstrap:'||p_league_id,0));

 select season,id into active_year,active_id from public.league_seasons
 where league_id=p_league_id and is_active for share;
 if active_id is null then raise exception 'exactly one canonical active season required';end if;

 select count(*) into source_count from public.contracts where league_id=p_league_id;
 if source_count<>p_expected_source_count then raise exception 'legacy contract source count changed: expected %, found %',p_expected_source_count,source_count;end if;

 if exists(
  select 1 from public.contracts c
  where c.league_id=p_league_id and (
   nullif(btrim(c.sleeper_player_id),'') is null or nullif(btrim(c.owner_name),'') is null
   or c.salary is null or c.salary<0 or c.contract_years_left is null or c.contract_years_left<1
   or c.contract_total_years is null or c.contract_total_years<c.contract_years_left))
 then raise exception 'legacy contract identity, salary, or term is invalid';end if;
 if exists(select 1 from public.contracts c where c.league_id=p_league_id group by c.sleeper_player_id having count(*)<>1)
 then raise exception 'duplicate live player ownership in legacy contracts';end if;
 if exists(
  select 1 from public.contracts c left join lateral(
   select count(*) n from public.league_teams t
   where t.league_id=c.league_id and btrim(t.owner_name)=btrim(c.owner_name)) m on true
  where c.league_id=p_league_id and m.n<>1)
 then raise exception 'legacy owner does not map to exactly one canonical league team';end if;
 if exists(select 1 from public.contracts c left join public.player_universe p on p.sleeper_id=c.sleeper_player_id
  where c.league_id=p_league_id and p.sleeper_id is null)
 then raise exception 'legacy player identity is absent from canonical player universe';end if;

 select public.rollover_material_fingerprint(coalesce(jsonb_agg(jsonb_build_object(
  'id',c.id,'player',c.sleeper_player_id,'owner',btrim(c.owner_name),'salary',c.salary,
  'years_left',c.contract_years_left,'total_years',c.contract_total_years,'rookie',coalesce(c.is_rookie,false))
  order by c.sleeper_player_id,c.id),'[]'::jsonb)) into source_fp
 from public.contracts c where c.league_id=p_league_id;
 execution_key:='legacy-canonical-bootstrap:'||p_league_id||':'||active_year||':v1';
 select * into existing from public.contract_backfill_executions where idempotency_key=execution_key for share;
 if existing.id is not null then
  if existing.status<>'validated' or existing.source_fingerprint<>source_fp
   or existing.row_counts->>'agreements'<>source_count::text then raise exception 'legacy bootstrap replay conflicts with recorded authority';end if;
  return jsonb_build_object('idempotent',true,'source_count',source_count,'source_fingerprint',source_fp,'row_counts',existing.row_counts);
 end if;
 if exists(select 1 from public.contract_agreements where league_id=p_league_id)
 then raise exception 'canonical agreements already exist; legacy bootstrap will not merge or overwrite';end if;

 -- Future rows are schedule metadata only. Existing rows are never overwritten.
 insert into public.league_seasons(league_id,season,sleeper_league_id,is_active,status)
 select p_league_id,y,null,false,'scheduled'
 from generate_series(active_year+1,active_year+(select max(contract_years_left)-1 from public.contracts where league_id=p_league_id)) y
 on conflict(league_id,season) do nothing;
 if exists(select 1 from public.contracts c cross join lateral generate_series(active_year,active_year+c.contract_years_left-1) y
  left join public.league_seasons ls on ls.league_id=p_league_id and ls.season=y
  where c.league_id=p_league_id and (ls.id is null or (y=active_year and not ls.is_active) or (y>active_year and ls.is_active)))
 then raise exception 'canonical contract season schedule is incomplete or contradictory';end if;

 insert into public.contract_backfill_executions(league_id,source_season,idempotency_key,source_fingerprint,status)
 values(p_league_id,active_year,execution_key,source_fp,'validating');
 insert into public.contract_agreements(league_id,league_team_id,player_id,sleeper_player_id,contract_type,origin,
  signed_season,start_season,end_season,status,source_legacy_contract_id)
 select c.league_id,t.id,c.sleeper_player_id,c.sleeper_player_id,
  case when coalesce(c.is_rookie,false) then 'rookie' else 'veteran' end,'imported_initial_contract',
  active_year-(c.contract_total_years-c.contract_years_left),active_year-(c.contract_total_years-c.contract_years_left),
  active_year+c.contract_years_left-1,'active',c.id
 from public.contracts c join public.league_teams t on t.league_id=c.league_id and btrim(t.owner_name)=btrim(c.owner_name)
 where c.league_id=p_league_id;
 get diagnostics agreement_count=row_count;

 insert into public.contract_seasons(contract_id,league_season_id,league_id,league_team_id,player_id,season,
  salary,guaranteed_salary,cap_hit,obligation_status,is_option_year,source,source_legacy_contract_id)
 select a.id,ls.id,a.league_id,a.league_team_id,a.player_id,y,c.salary,0,c.salary,
  case when y=active_year then 'active' else 'scheduled' end,false,'legacy_2026_canonical_bootstrap',c.id
 from public.contracts c join public.contract_agreements a on a.source_legacy_contract_id=c.id
 cross join lateral generate_series(active_year,active_year+c.contract_years_left-1) y
 join public.league_seasons ls on ls.league_id=c.league_id and ls.season=y
 where c.league_id=p_league_id;
 get diagnostics season_count=row_count;

 insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,
  new_values,metadata,idempotency_key)
 select a.id,a.league_id,a.league_team_id,a.player_id,'imported',active_year,'legacy_2026_canonical_bootstrap',
  jsonb_build_object('salary',c.salary,'years_remaining',c.contract_years_left,'total_years',c.contract_total_years),
  jsonb_build_object('source_legacy_contract_id',c.id,'source_fingerprint',source_fp),execution_key||':'||c.id
 from public.contracts c join public.contract_agreements a on a.source_legacy_contract_id=c.id where c.league_id=p_league_id;
 get diagnostics event_count=row_count;
 accepted_count:=agreement_count;
 if accepted_count<>source_count or event_count<>source_count
  or season_count<>(select sum(contract_years_left) from public.contracts where league_id=p_league_id)
 then raise exception 'legacy bootstrap reconciliation failed';end if;
 update public.contract_backfill_executions set status='validated',completed_at=clock_timestamp(),
  row_counts=jsonb_build_object('source',source_count,'accepted',accepted_count,'rejected',0,'agreements',agreement_count,'contract_seasons',season_count,'events',event_count)
 where idempotency_key=execution_key;
 return jsonb_build_object('idempotent',false,'source_count',source_count,'source_fingerprint',source_fp,
  'row_counts',jsonb_build_object('source',source_count,'accepted',accepted_count,'rejected',0,'agreements',agreement_count,'contract_seasons',season_count,'events',event_count));
end$$;

revoke all on function public.bootstrap_legacy_contracts_private(uuid,integer) from public,anon,authenticated,service_role;
grant execute on function public.bootstrap_legacy_contracts_private(uuid,integer) to service_role;

-- is_active is the canonical runtime discriminator. Status remains lifecycle
-- metadata and must not create a second, conflicting active-season definition.
do $$declare d text;begin
 select pg_get_functiondef('public.acquire_offseason_player_private(jsonb,uuid)'::regprocedure) into d;
 d:=replace(d,'where league_id=lid and is_active and status=''active'' for share','where league_id=lid and is_active for share');
 d:=replace(d,'(season=active_season and status=''active'' and is_active)','(season=active_season and is_active)');
 execute d;
 select pg_get_functiondef('public.release_offseason_player_authenticated(jsonb)'::regprocedure) into d;
 d:=replace(d,'where league_id=lid and is_active and status=''active'' for share','where league_id=lid and is_active for share');
 execute d;
end$$;

-- Normalize lifecycle metadata for the one affected league so the separately
-- certified 2026->2027 rollover preflight (which validates lifecycle state) is
-- compatible with the canonical runtime authority.
update public.league_seasons set status='active'
where league_id='b03edc51-bec1-4064-9201-72e48ba413f9' and is_active and status is null;

do $$begin
 if exists(select 1 from public.leagues where id='b03edc51-bec1-4064-9201-72e48ba413f9') then
  perform public.bootstrap_legacy_contracts_private('b03edc51-bec1-4064-9201-72e48ba413f9',92);
 end if;
end$$;

commit;
