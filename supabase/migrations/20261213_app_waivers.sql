-- Pickups and drops in the app (playoffs on, then every week once the league leaves Sleeper).
--
-- * FAAB bids (waiver_claims) are processed Wednesday and Sunday at 3:00 AM Eastern. Highest bid wins; a tie goes to
--   the earlier claim. The winner signs the player for this season at a salary equal to the bid ($1 minimum), the
--   same rule the Sleeper sync used. FAAB lives in faab_ledger (a starting balance at cutover, minus each win).
-- * Sunday from 3:00 AM Eastern to midnight, any free agent can be added straight away at $1 (no bid).
-- * Dropping a player charges dead cap for every season still owed at the league's rate ($1 contracts free),
--   like the Sleeper sync did.
-- * A player whose game has kicked off this week can't be added or dropped.
-- Everything is behind league_app_transactions.enabled.
begin;
set local search_path = pg_catalog, public;

-- 1. App acquisition kinds: acquire_offseason_player_private as 20261115 left it, with app_waiver/app_free_agent added and
--    20261109's identity step (copy a free agent into player_universe) kept, since 20261115's verbatim copy dropped it.
create or replace function public.acquire_offseason_player_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=(p_request->>'league_id')::uuid;tid uuid:=(p_request->>'league_team_id')::uuid;
 pid text:=p_request->>'player_id';yr int:=(p_request->>'season')::int;term int:=(p_request->>'years')::int;
 amount numeric:=(p_request->>'salary')::numeric;kind text:=p_request->>'acquisition_type';ikey text:=p_request->>'idempotency_key';
 agreement_id uuid;season_row public.league_seasons%rowtype;active_season int;i int;required_count int;existing public.contract_events%rowtype;
 material jsonb;request_fp text;agreement_status text;first_obligation_status text;
begin
 if kind not in('commissioner_manual_add','fa_auction','rookie_draft','sleeper_waiver','sleeper_free_agent','app_waiver','app_free_agent') or amount<0 or term<1 or nullif(ikey,'') is null then raise exception 'invalid offseason acquisition request';end if;
 material:=jsonb_build_object('operation','offseason_acquisition_v1','league_id',lid,'league_team_id',tid,'player_id',pid,'season',yr,'salary',amount,'years',term,'acquisition_type',kind,'notes',coalesce(p_request->>'notes',''));
 request_fp:=public.rollover_material_fingerprint(material);
 perform pg_advisory_xact_lock(hashtextextended('offseason-event:'||ikey,0));
 select * into existing from public.contract_events where idempotency_key=ikey;
 if existing.id is not null then
  if existing.event_type<>'signed' or existing.league_id<>lid or existing.league_team_id<>tid or existing.player_id<>pid
   or existing.source<>kind or existing.metadata->>'request_fingerprint' is distinct from request_fp then raise exception 'offseason idempotency key conflict';end if;
  return jsonb_build_object('idempotent',true,'contract_agreement_id',existing.contract_id,'league_team_id',tid,'player_id',pid);
 end if;
 perform public.assert_no_active_rollover_cutover_lock(lid);
 perform pg_advisory_xact_lock(hashtextextended('offseason-player:'||lid||':'||pid,0));
 if not exists(select 1 from public.league_teams where id=tid and league_id=lid) then raise exception 'canonical acquisition identity invalid';end if; perform public.ensure_acquirable_sleeper_identity_private(pid);
 select season into active_season from public.league_seasons where league_id=lid and is_active and status='active' for share;
 if active_season is null then raise exception 'acquisition season is not authorized by canonical season authority';end if;
 if public.player_has_live_contract_private(lid,pid,active_season) then raise exception 'player already has canonical active ownership';end if;
 if active_season is null or (kind<>'rookie_draft' and yr<>active_season) or (kind='rookie_draft' and yr not in(active_season,active_season+1)) then raise exception 'acquisition season is not authorized by canonical season authority';end if;
 if yr=active_season+1 and not exists(select 1 from public.league_seasons where league_id=lid and season=yr and status='scheduled' and not is_active) then raise exception 'upcoming rookie draft season is not canonical scheduled season';end if;
 select count(*) into required_count from public.league_seasons where league_id=lid and season between yr and yr+term-1
  and ((season=active_season and status='active' and is_active) or (season>active_season and status='scheduled' and not is_active));
 if required_count<>term then raise exception 'complete canonical contract season schedule required';end if;
 perform 1 from public.league_seasons where league_id=lid and season between yr and yr+term-1 order by season for share;
 agreement_status:=case when yr=active_season then 'active' else 'scheduled' end;
 first_obligation_status:=case when yr=active_season then 'active' else 'scheduled' end;
 insert into public.contract_agreements(league_id,league_team_id,player_id,sleeper_player_id,contract_type,origin,signed_season,start_season,end_season,status)
 values(lid,tid,pid,pid,case when kind='rookie_draft' then 'rookie' else 'veteran' end,
 case when kind='commissioner_manual_add' then 'commissioner_adjustment' else 'signed' end,yr,yr,yr+term-1,agreement_status) returning id into agreement_id;
 for i in 0..term-1 loop
  select * into season_row from public.league_seasons where league_id=lid and season=yr+i;
  insert into public.contract_seasons(contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,guaranteed_salary,cap_hit,dead_cap_if_released,obligation_status,source)
  values(agreement_id,season_row.id,lid,tid,pid,yr+i,amount,0,amount,amount,case when i=0 then first_obligation_status else 'scheduled' end,kind);
 end loop;
 insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,new_values,metadata,idempotency_key)
 values(agreement_id,lid,tid,pid,'signed',yr,kind,p_actor,jsonb_build_object('salary',amount,'years',term,'team',tid),jsonb_build_object('notes',coalesce(p_request->>'notes',''),'acquisition_type',kind,'request_fingerprint',request_fp,'request_material',material),ikey);
 return jsonb_build_object('idempotent',false,'contract_agreement_id',agreement_id,'league_team_id',tid,'player_id',pid);
end$$;

-- 2. Owner-safe release: the commissioner release body as a private function (source recorded on the events),
--    with the commissioner wrapper calling it after its authority check.
create or replace function public.release_offseason_player_private(p_request jsonb,p_actor uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=p_actor;src text:=coalesce(nullif(p_request->>'source',''),'commissioner_manual_drop');lid uuid:=(p_request->>'league_id')::uuid;tid uuid:=(p_request->>'league_team_id')::uuid;
 pid text:=p_request->>'player_id';yr int:=(p_request->>'season')::int;ikey text:=p_request->>'idempotency_key';dead numeric:=coalesce((p_request->>'dead_cap')::numeric,0);
 agreement public.contract_agreements%rowtype;existing public.contract_events%rowtype;release_event_id uuid;dead_event_id uuid;active_season int;
 material jsonb;request_fp text;
begin
 perform public.assert_no_active_rollover_cutover_lock(lid);
 if dead<0 or nullif(ikey,'') is null then raise exception 'invalid offseason release request';end if;
 material:=jsonb_build_object('operation','offseason_release_v1','league_id',lid,'league_team_id',tid,'player_id',pid,'season',yr,'dead_cap',dead,'notes',coalesce(p_request->>'notes',''));
 request_fp:=public.rollover_material_fingerprint(material);
 perform pg_advisory_xact_lock(hashtextextended('offseason-event:'||ikey,0));
 select * into existing from public.contract_events where idempotency_key=ikey;
 if existing.id is not null then
  if existing.event_type<>'released' or existing.league_id<>lid or existing.league_team_id<>tid or existing.player_id<>pid
   or existing.source<>src or existing.metadata->>'request_fingerprint' is distinct from request_fp then raise exception 'offseason idempotency key conflict';end if;
  return jsonb_build_object('idempotent',true,'contract_agreement_id',existing.contract_id,'player_id',pid,'dead_cap',dead);
 end if;
 select season into active_season from public.league_seasons where league_id=lid and is_active and status='active' for share;
 if active_season is null or yr<>active_season then raise exception 'release season is not canonical active season';end if;
 select * into agreement from public.contract_agreements where league_id=lid and league_team_id=tid and player_id=pid and status in('active','scheduled') and superseded_by_contract_id is null for update;
 if agreement.id is null then raise exception 'canonical active ownership not found for team';end if;
 update public.contract_agreements set status='released',updated_at=clock_timestamp() where id=agreement.id;
 perform set_config('app.contract_transition_execution','contract-transition-executor-v1',true);
 update public.contract_seasons set obligation_status=case when season=yr and obligation_status='active' then 'released' else 'voided' end,updated_at=clock_timestamp()
  where contract_id=agreement.id and obligation_status in('active','scheduled');
 insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)
 values(agreement.id,lid,tid,pid,'released',yr,src,actor,jsonb_build_object('status',agreement.status),jsonb_build_object('status','released','dead_cap',dead),jsonb_build_object('notes',coalesce(p_request->>'notes',''),'dead_cap',dead,'request_fingerprint',request_fp,'request_material',material),ikey) returning id into release_event_id;
 if dead>0 then
  insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,new_values,metadata,idempotency_key)
  values(agreement.id,lid,tid,pid,'dead_cap_created',yr,src,actor,jsonb_build_object('dead_cap_amount',dead),jsonb_build_object('dead_cap_amount',dead,'penalty_rule',src,'release_event_id',release_event_id,'request_fingerprint',request_fp),ikey||':dead-cap-event') returning id into dead_event_id;
  insert into public.dead_cap_obligations(league_id,league_team_id,player_id,contract_agreement_id,season,amount,source_event_id,termination_type,calculation_rule,status,created_by,metadata,idempotency_key)
  values(lid,tid,pid,agreement.id,yr,dead,dead_event_id,'commissioner_adjustment','commissioner_supplied_manual_drop','active',actor,jsonb_build_object('notes',coalesce(p_request->>'notes',''),'release_event_id',release_event_id),ikey||':dead-cap');
 end if;
 return jsonb_build_object('idempotent',false,'contract_agreement_id',agreement.id,'player_id',pid,'dead_cap',dead);
end$$;

create or replace function public.release_offseason_player_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();
begin
 perform public.require_commissioner_authority((p_request->>'league_id')::uuid);
 return public.release_offseason_player_private(p_request - 'source',actor);
end$$;

-- 3. FAAB and claims.
create table if not exists public.faab_ledger(
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 league_team_id uuid not null references public.league_teams(id),
 season int not null,
 amount numeric(8,2) not null,          -- + starting balance or credit, - a winning bid
 reason text not null check(reason in('start','waiver','adjust')),
 ref_id uuid,
 note text,
 created_at timestamptz not null default clock_timestamp()
);
create unique index if not exists faab_ledger_ref_idx on public.faab_ledger(ref_id) where ref_id is not null;
alter table public.faab_ledger enable row level security;
revoke all on public.faab_ledger from public,anon,authenticated;
grant select on public.faab_ledger to authenticated;
grant select,insert on public.faab_ledger to service_role;
drop policy if exists faab_ledger_member_read on public.faab_ledger;
create policy faab_ledger_member_read on public.faab_ledger for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=faab_ledger.league_id and m.user_id=auth.uid()));

create table if not exists public.waiver_claims(
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 league_team_id uuid not null references public.league_teams(id),
 season int not null,
 player_id text not null,
 bid numeric(8,2) not null check(bid>=0 and bid=round(bid)),
 drop_player_id text,
 status text not null default 'pending' check(status in('pending','won','lost','cancelled','failed')),
 process_at timestamptz not null,
 note text,
 winning_team_id uuid references public.league_teams(id),
 winning_bid numeric(8,2),
 contract_id uuid,
 created_by uuid not null references auth.users(id),
 created_at timestamptz not null default clock_timestamp(),
 decided_at timestamptz
);
create unique index if not exists waiver_claims_one_open_idx on public.waiver_claims(league_team_id,player_id) where status='pending';
create index if not exists waiver_claims_due_idx on public.waiver_claims(league_id,status,process_at);
alter table public.waiver_claims enable row level security;
revoke all on public.waiver_claims from public,anon,authenticated;
grant select on public.waiver_claims to authenticated;
grant select,insert,update on public.waiver_claims to service_role;
-- Open bids are private to the bidding team; once processed every bid is visible (the "N bids" view).
drop policy if exists waiver_claims_read on public.waiver_claims;
create policy waiver_claims_read on public.waiver_claims for select to authenticated
 using(exists(select 1 from public.league_memberships m where m.league_id=waiver_claims.league_id and m.user_id=auth.uid()
              and (waiver_claims.status in('won','lost') or m.league_team_id=waiver_claims.league_team_id)));

-- 4. Helpers.
create or replace function public.faab_balance_private(p_league_id uuid,p_team_id uuid,p_season int) returns numeric
language sql stable security definer set search_path=pg_catalog,public as $$
 select coalesce(sum(amount),0) from public.faab_ledger where league_id=p_league_id and league_team_id=p_team_id and season=p_season
$$;

-- The next bid run after now: Wednesday or Sunday, 3:00 AM Eastern.
create or replace function public.next_waiver_run_private() returns timestamptz
language sql stable set search_path=pg_catalog,public as $$
 select min(t) from (
  select ((date_trunc('day',now() at time zone 'America/New_York')+make_interval(days=>d)+time '03:00') at time zone 'America/New_York') t
  from generate_series(0,7) d
 ) x where t>now() and extract(isodow from t at time zone 'America/New_York') in(3,7)
$$;

-- Sunday after the 3:00 AM run until midnight Eastern: adds go through straight away.
create or replace function public.free_agency_open_private() returns boolean
language sql stable set search_path=pg_catalog,public as $$
 select extract(isodow from now() at time zone 'America/New_York')=7 and (now() at time zone 'America/New_York')::time>=time '03:00'
$$;

create or replace function public.roster_room_private(p_league_id uuid,p_team_id uuid,p_season int) returns int
language sql stable security definer set search_path=pg_catalog,public as $$
 select (select count(*)::int from public.league_ai_settings x, jsonb_array_elements_text(x.roster_positions) s
          where x.league_id=p_league_id and s not in('TAXI','IR'))
      - (select count(*)::int from public.contract_agreements a join public.contract_seasons cs on cs.contract_id=a.id
          where a.league_id=p_league_id and a.league_team_id=p_team_id and a.status='active' and a.superseded_by_contract_id is null
            and cs.season=p_season and cs.obligation_status='active'
            and not exists(select 1 from public.season_roster_assignments r join public.league_seasons ls on ls.id=r.league_season_id
                           where ls.league_id=p_league_id and ls.season=p_season and r.sleeper_player_id=a.player_id and r.roster_designation='taxi')
            and not exists(select 1 from public.cap_adjustments c where c.league_id=p_league_id and c.season=p_season
                           and c.adjustment_type='ir_adjustment' and c.sleeper_player_id=a.player_id))
$$;

-- Drop one of the team's players: dead cap for every season still owed at the league's rate ($1 seasons free).
create or replace function public.owner_drop_private(p_league_id uuid,p_team_id uuid,p_player_id text,p_season int,p_key text,p_actor uuid,p_note text)
returns numeric language plpgsql security definer set search_path=pg_catalog,public as $$
declare pct numeric;agreement uuid;cs record;now_amount numeric:=0;amt numeric;total numeric:=0;release_event uuid;dead_event uuid;
begin
 if public.player_locked_this_week_private(p_player_id) then raise exception 'player_locked';end if;
 select default_dead_cap_pct into pct from public.league_rules where league_id=p_league_id;
 if pct is null then raise exception 'dead_cap_rule_missing';end if;
 select a.id into agreement from public.contract_agreements a where a.league_id=p_league_id and a.league_team_id=p_team_id and a.player_id=p_player_id
  and a.status in('active','scheduled') and a.superseded_by_contract_id is null;
 if agreement is null then raise exception 'drop_not_yours';end if;
 for cs in select season,coalesce(cap_hit,salary) basis from public.contract_seasons
           where contract_id=agreement and season>=p_season and obligation_status in('active','scheduled') order by season loop
  amt:=case when cs.basis<=1 then 0 else round(cs.basis*pct/100,2) end;
  total:=total+amt;
  if cs.season=p_season then now_amount:=amt;end if;
 end loop;
 perform public.release_offseason_player_private(jsonb_build_object('league_id',p_league_id,'league_team_id',p_team_id,'player_id',p_player_id,
  'season',p_season,'dead_cap',now_amount,'idempotency_key',p_key,'source','owner_drop','notes',p_note),p_actor);
 select id into release_event from public.contract_events where idempotency_key=p_key;
 -- Later seasons, like append_release_dead_cap_schedule_authenticated does for Sleeper drops.
 for cs in select season,coalesce(cap_hit,salary) basis from public.contract_seasons where contract_id=agreement and season>p_season order by season loop
  amt:=case when cs.basis<=1 then 0 else round(cs.basis*pct/100,2) end;
  continue when amt<=0;
  insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,new_values,metadata,idempotency_key)
  values(agreement,p_league_id,p_team_id,p_player_id,'dead_cap_created',cs.season,'owner_drop',p_actor,jsonb_build_object('dead_cap_amount',amt),
         jsonb_build_object('dead_cap_amount',amt,'penalty_rule','owner_drop_multi_season','release_event_id',release_event),p_key||':dead-cap-event:'||cs.season)
  on conflict(idempotency_key) do nothing returning id into dead_event;
  continue when dead_event is null;
  insert into public.dead_cap_obligations(league_id,league_team_id,player_id,contract_agreement_id,season,amount,source_event_id,termination_type,calculation_rule,status,created_by,metadata,idempotency_key)
  values(p_league_id,p_team_id,p_player_id,agreement,cs.season,amt,dead_event,'early_termination','owner_drop_multi_season','active',p_actor,
         jsonb_build_object('release_event_id',release_event),p_key||':dead-cap:'||cs.season)
  on conflict(idempotency_key) do nothing;
  dead_event:=null;
 end loop;
 return total;
end$$;

create or replace function public.app_switch_private(p_league_id uuid) returns void
language plpgsql stable security definer set search_path=pg_catalog,public as $$
begin
 if not coalesce((select enabled from public.league_app_transactions where league_id=p_league_id),false) then raise exception 'transactions_not_enabled';end if;
end$$;

create or replace function public.active_season_private(p_league_id uuid) returns int
language sql stable security definer set search_path=pg_catalog,public as $$
 select season from public.league_seasons where league_id=p_league_id and is_active and status='active' limit 1
$$;

-- 5. Owner actions.
-- {league_id, player_id} -> {dead_cap}
create or replace function public.drop_player_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;me uuid;yr int;dead numeric;
begin
 perform public.app_switch_private(lid);
 me:=public.my_league_team_private(lid);
 if me is null then raise exception 'not_a_team_owner';end if;
 yr:=public.active_season_private(lid);
 dead:=public.owner_drop_private(lid,me,p_request->>'player_id',yr,'owner-drop:'||gen_random_uuid(),actor,'Dropped in the app');
 return jsonb_build_object('dead_cap',dead);
end$$;

-- Sunday free agency: {league_id, player_id, drop_player_id?} -> signs him at $1 for this season.
create or replace function public.add_free_agent_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;me uuid;yr int;
 pid text:=p_request->>'player_id';drop_pid text:=nullif(p_request->>'drop_player_id','');k text:='owner-add:'||gen_random_uuid();result jsonb;
begin
 perform public.app_switch_private(lid);
 me:=public.my_league_team_private(lid);
 if me is null then raise exception 'not_a_team_owner';end if;
 if not public.free_agency_open_private() then raise exception 'free_agency_closed';end if;
 yr:=public.active_season_private(lid);
 perform pg_advisory_xact_lock(hashtextextended('waivers:'||lid,0));
 if public.player_has_live_contract_private(lid,pid,yr) then raise exception 'player_taken';end if;
 if exists(select 1 from public.waiver_claims where league_id=lid and player_id=pid and status='pending') then raise exception 'player_on_waivers';end if;
 if public.player_locked_this_week_private(pid) then raise exception 'player_locked';end if;
 if drop_pid is not null then perform public.owner_drop_private(lid,me,drop_pid,yr,k||':drop',actor,'Dropped for a free agent in the app');end if;
 if public.roster_room_private(lid,me,yr)<1 then raise exception 'roster_full';end if;
 result:=public.acquire_offseason_player_private(jsonb_build_object('league_id',lid,'player_id',pid,'league_team_id',me,'season',yr,'salary',1,'years',1,
   'acquisition_type','app_free_agent','idempotency_key',k,'notes','Free agent added in the app'),actor);
 return jsonb_build_object('contract_id',result->>'contract_agreement_id','salary',1);
end$$;

-- {league_id, player_id, bid, drop_player_id?}: one open bid per player per team (a new bid replaces the old one).
create or replace function public.place_waiver_claim_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;me uuid;yr int;
 pid text:=p_request->>'player_id';amount numeric:=(p_request->>'bid')::numeric;drop_pid text:=nullif(p_request->>'drop_player_id','');c public.waiver_claims%rowtype;
begin
 perform public.app_switch_private(lid);
 me:=public.my_league_team_private(lid);
 if me is null then raise exception 'not_a_team_owner';end if;
 yr:=public.active_season_private(lid);
 if amount is null or amount<0 or amount<>round(amount) then raise exception 'bid_invalid';end if;
 if amount>public.faab_balance_private(lid,me,yr) then raise exception 'bid_over_budget';end if;
 if public.player_has_live_contract_private(lid,pid,yr) then raise exception 'player_taken';end if;
 if drop_pid is not null and not public.team_holds_player_private(lid,me,drop_pid,yr) then raise exception 'drop_not_yours';end if;
 update public.waiver_claims set status='cancelled',decided_at=clock_timestamp(),note='Replaced by a new bid'
  where league_team_id=me and player_id=pid and status='pending';
 insert into public.waiver_claims(league_id,league_team_id,season,player_id,bid,drop_player_id,process_at,created_by)
 values(lid,me,yr,pid,amount,drop_pid,public.next_waiver_run_private(),actor) returning * into c;
 return jsonb_build_object('claim_id',c.id,'process_at',c.process_at);
end$$;

create or replace function public.cancel_waiver_claim_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();c public.waiver_claims%rowtype;
begin
 select * into c from public.waiver_claims where id=(p_request->>'claim_id')::uuid for update;
 if c.id is null or c.status<>'pending' then raise exception 'claim_not_open';end if;
 if c.league_team_id is distinct from public.my_league_team_private(c.league_id) then raise exception 'not_your_claim';end if;
 update public.waiver_claims set status='cancelled',decided_at=clock_timestamp() where id=c.id;
 return jsonb_build_object('claim_id',c.id,'status','cancelled');
end$$;

-- 6. The bid run. Safe to call any time by anyone in the league (or the scheduled job): it only touches claims
--    whose run time has passed. Highest bid first; a tie goes to the earlier claim.
create or replace function public.process_due_waivers(p_league_id uuid) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare c public.waiver_claims%rowtype;yr int;result jsonb;won int:=0;lost int:=0;failed int:=0;why text;
begin
 if auth.uid() is not null and not exists(select 1 from public.league_memberships where league_id=p_league_id and user_id=auth.uid()) then
  raise exception 'not_a_member';end if;
 if not coalesce((select enabled from public.league_app_transactions where league_id=p_league_id),false) then return jsonb_build_object('processed',0);end if;
 if not exists(select 1 from public.waiver_claims where league_id=p_league_id and status='pending' and process_at<=now()) then return jsonb_build_object('processed',0);end if;
 perform pg_advisory_xact_lock(hashtextextended('waivers:'||p_league_id,0));
 for c in select * from public.waiver_claims where league_id=p_league_id and status='pending' and process_at<=now()
          order by bid desc,created_at for update loop
  yr:=c.season;why:=null;
  if public.player_has_live_contract_private(c.league_id,c.player_id,yr) then
   update public.waiver_claims w set status='lost',decided_at=clock_timestamp(),
     winning_team_id=(select x.league_team_id from public.waiver_claims x where x.league_id=c.league_id and x.player_id=c.player_id and x.status='won' and x.process_at=c.process_at limit 1),
     winning_bid=(select x.bid from public.waiver_claims x where x.league_id=c.league_id and x.player_id=c.player_id and x.status='won' and x.process_at=c.process_at limit 1),
     note=case when exists(select 1 from public.waiver_claims x where x.league_id=c.league_id and x.player_id=c.player_id and x.status='won' and x.process_at=c.process_at)
               then 'Outbid' else 'Signed by another team' end where w.id=c.id;
   lost:=lost+1;continue;
  end if;
  if c.bid>public.faab_balance_private(c.league_id,c.league_team_id,yr) then why:='Not enough FAAB left';
  elsif public.player_locked_this_week_private(c.player_id) then why:='His game had already started';
  elsif c.drop_player_id is not null and not public.team_holds_player_private(c.league_id,c.league_team_id,c.drop_player_id,yr) then why:='The player to drop is no longer on your roster';
  elsif c.drop_player_id is not null and public.player_locked_this_week_private(c.drop_player_id) then why:='The player to drop had already played';
  elsif c.drop_player_id is null and public.roster_room_private(c.league_id,c.league_team_id,yr)<1 then why:='Roster full';
  end if;
  if why is not null then
   update public.waiver_claims set status='failed',decided_at=clock_timestamp(),note=why where id=c.id;failed:=failed+1;continue;
  end if;
  begin
   if c.drop_player_id is not null then
    perform public.owner_drop_private(c.league_id,c.league_team_id,c.drop_player_id,yr,'waiver-drop:'||c.id,c.created_by,'Dropped for a waiver claim');
   end if;
   result:=public.acquire_offseason_player_private(jsonb_build_object('league_id',c.league_id,'player_id',c.player_id,'league_team_id',c.league_team_id,
     'season',yr,'salary',greatest(c.bid,1),'years',1,'acquisition_type','app_waiver','idempotency_key','waiver:'||c.id,
     'notes','Won on waivers for $'||c.bid||' FAAB'),c.created_by);
   insert into public.faab_ledger(league_id,league_team_id,season,amount,reason,ref_id,note)
   values(c.league_id,c.league_team_id,yr,-c.bid,'waiver',c.id,'Waiver win') on conflict do nothing;
   update public.waiver_claims set status='won',decided_at=clock_timestamp(),winning_team_id=c.league_team_id,winning_bid=c.bid,
     contract_id=(result->>'contract_agreement_id')::uuid where id=c.id;
   won:=won+1;
  exception when others then
   update public.waiver_claims set status='failed',decided_at=clock_timestamp(),note='Not processed: '||left(sqlerrm,120) where id=c.id;failed:=failed+1;
  end;
 end loop;
 return jsonb_build_object('processed',won+lost+failed,'won',won,'lost',lost,'failed',failed);
end$$;

-- 7. Grants.
revoke all on function public.release_offseason_player_private(jsonb,uuid),public.faab_balance_private(uuid,uuid,int),
 public.roster_room_private(uuid,uuid,int),public.owner_drop_private(uuid,uuid,text,int,text,uuid,text),public.app_switch_private(uuid),
 public.active_season_private(uuid) from public,anon,authenticated;
grant execute on function public.faab_balance_private(uuid,uuid,int) to authenticated;
revoke all on function public.drop_player_authenticated(jsonb),public.add_free_agent_authenticated(jsonb),public.place_waiver_claim_authenticated(jsonb),
 public.cancel_waiver_claim_authenticated(jsonb),public.process_due_waivers(uuid) from public,anon;
grant execute on function public.drop_player_authenticated(jsonb),public.add_free_agent_authenticated(jsonb),public.place_waiver_claim_authenticated(jsonb),
 public.cancel_waiver_claim_authenticated(jsonb),public.process_due_waivers(uuid) to authenticated;
grant execute on function public.process_due_waivers(uuid) to service_role;

commit;
