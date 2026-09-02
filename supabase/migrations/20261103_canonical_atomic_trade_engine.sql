-- Canonical multi-team trade execution, retained salary, and rookie draft lifecycle.

create table public.league_draft_lifecycles(
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 season integer not null check(season between 1900 and 9999),
 status text not null check(status in('scheduled','in_progress','completed')),
 expected_pick_count integer not null check(expected_pick_count>=0),
 recorded_pick_count integer not null default 0 check(recorded_pick_count>=0),
 completed_at timestamptz,
 completed_by uuid references auth.users(id),
 created_at timestamptz not null default clock_timestamp(),
 updated_at timestamptz not null default clock_timestamp(),
 unique(league_id,season),
 check(recorded_pick_count<=expected_pick_count),
 check((status='completed' and completed_at is not null and completed_by is not null)or(status<>'completed' and completed_at is null and completed_by is null))
);

alter table public.draft_pick_assets drop constraint draft_pick_assets_asset_status_check;
alter table public.draft_pick_assets add constraint draft_pick_assets_asset_status_check check(asset_status in('tradable','consumed','historical'));
alter table public.draft_pick_assets add column rookie_draft_assignment_id uuid unique references public.rookie_draft_board_assignments(id);

create table public.canonical_trades(
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 status text not null check(status='completed'),
 idempotency_key text not null,
 request_fingerprint text not null check(request_fingerprint~'^[0-9a-f]{64}$'),
 notes text,
 created_by uuid not null references auth.users(id),
 completed_at timestamptz not null default clock_timestamp(),
 created_at timestamptz not null default clock_timestamp(),
 unique(league_id,idempotency_key)
);

create table public.canonical_trade_participants(
 trade_id uuid not null references public.canonical_trades(id) on delete restrict,
 league_id uuid not null references public.leagues(id),
 league_team_id uuid not null references public.league_teams(id),
 created_at timestamptz not null default clock_timestamp(),
 primary key(trade_id,league_team_id)
);

create table public.canonical_trade_asset_movements(
 id uuid primary key default gen_random_uuid(),
 trade_id uuid not null references public.canonical_trades(id) on delete restrict,
 league_id uuid not null references public.leagues(id),
 asset_type text not null check(asset_type in('player','draft_pick')),
 contract_agreement_id uuid references public.contract_agreements(id),
 player_id text,
 draft_pick_id uuid references public.draft_pick_assets(stable_pick_id),
 from_league_team_id uuid not null references public.league_teams(id),
 to_league_team_id uuid not null references public.league_teams(id),
 asset_snapshot jsonb not null,
 created_at timestamptz not null default clock_timestamp(),
 check(from_league_team_id<>to_league_team_id),
 check((asset_type='player' and contract_agreement_id is not null and player_id is not null and draft_pick_id is null)or(asset_type='draft_pick' and draft_pick_id is not null and contract_agreement_id is null and player_id is null)),
 unique(trade_id,contract_agreement_id),
 unique(trade_id,draft_pick_id)
);

create table public.trade_retained_salary_obligations(
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 trade_id uuid not null references public.canonical_trades(id) on delete restrict,
 player_id text not null references public.player_universe(sleeper_id),
 contract_agreement_id uuid not null references public.contract_agreements(id),
 contract_season_id uuid not null references public.contract_seasons(id),
 retaining_league_team_id uuid not null references public.league_teams(id),
 receiving_league_team_id_at_creation uuid not null references public.league_teams(id),
 season integer not null,
 amount numeric(12,2) not null check(amount>0),
 status text not null check(status in('active','satisfied','voided')),
 reason text not null check(reason='trade_retained_salary'),
 source text not null check(source='canonical_trade_rpc'),
 created_by uuid not null references auth.users(id),
 created_at timestamptz not null default clock_timestamp(),
 unique(trade_id,contract_season_id,retaining_league_team_id)
);
create index trade_retained_salary_team_season_idx on public.trade_retained_salary_obligations(league_id,retaining_league_team_id,season,status);
create index trade_retained_salary_contract_season_idx on public.trade_retained_salary_obligations(contract_season_id,status);

alter table public.league_draft_lifecycles enable row level security;
alter table public.canonical_trades enable row level security;
alter table public.canonical_trade_participants enable row level security;
alter table public.canonical_trade_asset_movements enable row level security;
alter table public.trade_retained_salary_obligations enable row level security;
revoke all on public.league_draft_lifecycles,public.canonical_trades,public.canonical_trade_participants,public.canonical_trade_asset_movements,public.trade_retained_salary_obligations from public,anon,authenticated;
grant select,insert,update,delete on public.league_draft_lifecycles,public.canonical_trades,public.canonical_trade_participants,public.canonical_trade_asset_movements,public.trade_retained_salary_obligations to service_role;

create policy draft_lifecycle_member_read on public.league_draft_lifecycles for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=league_draft_lifecycles.league_id and m.user_id=auth.uid()));
create policy canonical_trades_member_read on public.canonical_trades for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=canonical_trades.league_id and m.user_id=auth.uid()));
create policy canonical_trade_participants_member_read on public.canonical_trade_participants for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=canonical_trade_participants.league_id and m.user_id=auth.uid()));
create policy canonical_trade_movements_member_read on public.canonical_trade_asset_movements for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=canonical_trade_asset_movements.league_id and m.user_id=auth.uid()));
create policy retained_salary_member_read on public.trade_retained_salary_obligations for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=trade_retained_salary_obligations.league_id and m.user_id=auth.uid()));

create or replace function public.configure_draft_lifecycle_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;yr int:=(p_request->>'season')::int;expected int:=(p_request->>'expected_pick_count')::int;requested_status text:=coalesce(nullif(p_request->>'status',''),'scheduled');r public.league_draft_lifecycles%rowtype;
begin
 perform public.require_commissioner_authority(lid);
 if requested_status not in('scheduled','in_progress')or expected<0 then raise exception 'draft_lifecycle_configuration_invalid';end if;
 insert into public.league_draft_lifecycles(league_id,season,status,expected_pick_count,recorded_pick_count)
 values(lid,yr,requested_status,expected,(select count(*) from public.rookie_draft_board_assignments where league_id=lid and draft_year=yr))
 on conflict(league_id,season) do update set status=excluded.status,expected_pick_count=excluded.expected_pick_count,recorded_pick_count=excluded.recorded_pick_count,updated_at=clock_timestamp()
 where league_draft_lifecycles.status<>'completed'
 returning * into r;
 if r.id is null then raise exception 'completed_draft_lifecycle_immutable';end if;
 return jsonb_build_object('id',r.id,'league_id',r.league_id,'season',r.season,'status',r.status,'expected_pick_count',r.expected_pick_count,'recorded_pick_count',r.recorded_pick_count);
end$$;

create or replace function public.initialize_draft_inventory_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;yr int:=(p_request->>'season')::int;slots jsonb:=p_request->'slots';lifecycle public.league_draft_lifecycles%rowtype;class public.draft_inventory_classes%rowtype;asset public.draft_pick_assets%rowtype;assignment public.rookie_draft_board_assignments%rowtype;item jsonb;team_count int;round_count int;created_count int:=0;linked_count int:=0;original_id uuid;current_id uuid;assignment_id uuid;round_number int;stable_id uuid;provenance jsonb;
begin
 perform public.require_commissioner_authority(lid);
 perform pg_advisory_xact_lock(hashtextextended('canonical-draft-inventory:'||lid::text||':'||yr::text,0));
 select * into lifecycle from public.league_draft_lifecycles where league_id=lid and season=yr for update;
 if lifecycle.id is null then raise exception 'draft_lifecycle_missing';end if;
 if lifecycle.status='completed' then raise exception 'completed_draft_lifecycle_immutable';end if;
 select count(*) into team_count from public.league_teams where league_id=lid;
 if lifecycle.expected_pick_count<=0 or team_count<=0 or lifecycle.expected_pick_count%team_count<>0 then raise exception 'draft_inventory_structure_invalid';end if;
 round_count:=lifecycle.expected_pick_count/team_count;
 if jsonb_typeof(slots)<>'array' or jsonb_array_length(slots)<>lifecycle.expected_pick_count then raise exception 'draft_inventory_slot_count_mismatch';end if;
 if exists(select 1 from jsonb_array_elements(slots)s group by (s->>'round_number')::int,(s->>'original_league_team_id')::uuid having count(*)<>1)then raise exception 'draft_inventory_slot_duplicate';end if;
 if exists(select 1 from generate_series(1,round_count)r cross join public.league_teams t where t.league_id=lid and not exists(select 1 from jsonb_array_elements(slots)s where (s->>'round_number')::int=r and (s->>'original_league_team_id')::uuid=t.id))then raise exception 'draft_inventory_slot_missing';end if;
 if exists(select 1 from jsonb_array_elements(slots)s left join public.league_teams o on o.id=(s->>'original_league_team_id')::uuid and o.league_id=lid left join public.league_teams c on c.id=(s->>'current_owner_league_team_id')::uuid and c.league_id=lid where o.id is null or c.id is null)then raise exception 'draft_inventory_cross_league_team';end if;
 select * into class from public.draft_inventory_classes where league_id=lid and draft_year=yr for update;
 if class.id is null then
  insert into public.draft_inventory_classes(league_id,draft_year,horizon_status,publication_status,provenance,deterministic_class_hash)
  values(lid,yr,'visible_prepared','unpublished',jsonb_build_object('source','canonical_trade_inventory_initializer','actor',actor),public.rollover_material_fingerprint(jsonb_build_object('schema','canonical-draft-inventory-class-v1','league_id',lid,'season',yr,'team_count',team_count,'round_count',round_count)))returning * into class;
 end if;
 for item in select value from jsonb_array_elements(slots)loop
  round_number:=(item->>'round_number')::int;original_id:=(item->>'original_league_team_id')::uuid;current_id:=(item->>'current_owner_league_team_id')::uuid;assignment_id:=nullif(item->>'rookie_draft_assignment_id','')::uuid;provenance:=item->'provenance';
  if round_number not between 1 and round_count or jsonb_typeof(provenance)<>'object' or nullif(provenance->>'source','') is null or nullif(provenance->>'evidence_id','') is null then raise exception 'draft_inventory_slot_evidence_invalid';end if;
  if assignment_id is not null then
   select * into assignment from public.rookie_draft_board_assignments where id=assignment_id and league_id=lid and draft_year=yr for share;
   if assignment.id is null or assignment.draft_round<>round_number or assignment.original_league_team_id<>current_id then raise exception 'draft_inventory_assignment_reconciliation_invalid';end if;
  end if;
  stable_id:=public.phase3b9a_stable_pick_id(lid,yr,round_number,original_id);
  select * into asset from public.draft_pick_assets where stable_pick_id=stable_id for update;
  if asset.stable_pick_id is null then
   insert into public.draft_pick_assets(stable_pick_id,class_id,league_id,draft_year,round_number,original_league_team_id,current_owner_league_team_id,asset_status,publication_status,trade_provenance,generation_provenance,deterministic_asset_hash,rookie_draft_assignment_id)
   values(stable_id,class.id,lid,yr,round_number,original_id,current_id,'tradable','unpublished','[]',provenance||jsonb_build_object('initializer','canonical_trade_inventory_initializer'),public.rollover_material_fingerprint(jsonb_build_object('league_id',lid,'season',yr,'round_number',round_number,'original_league_team_id',original_id,'current_owner_league_team_id',current_id,'provenance',provenance)),assignment_id);created_count:=created_count+1;
  else
   if asset.class_id<>class.id or asset.league_id<>lid or asset.draft_year<>yr or asset.round_number<>round_number or asset.original_league_team_id<>original_id or asset.current_owner_league_team_id<>current_id then raise exception 'draft_inventory_replay_conflict';end if;
   if asset.rookie_draft_assignment_id is not null and asset.rookie_draft_assignment_id is distinct from assignment_id then raise exception 'draft_inventory_assignment_replay_conflict';end if;
   if asset.rookie_draft_assignment_id is null and assignment_id is not null then update public.draft_pick_assets set rookie_draft_assignment_id=assignment_id where stable_pick_id=stable_id;linked_count:=linked_count+1;end if;
  end if;
 end loop;
 if(select count(*) from public.draft_pick_assets where league_id=lid and draft_year=yr)<>lifecycle.expected_pick_count then raise exception 'draft_inventory_postcondition_failed';end if;
 return jsonb_build_object('league_id',lid,'season',yr,'expected_pick_count',lifecycle.expected_pick_count,'created_asset_count',created_count,'linked_assignment_count',linked_count,'idempotent',created_count=0 and linked_count=0);
end$$;

create or replace function public.complete_rookie_draft_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;yr int:=(p_request->>'season')::int;r public.league_draft_lifecycles%rowtype;recorded int;asset_count int;team_count int;round_count int;locked_count int;
begin
 perform public.require_commissioner_authority(lid);
 select * into r from public.league_draft_lifecycles where league_id=lid and season=yr for update;
 if r.id is null then raise exception 'draft_lifecycle_missing';end if;
 if r.status='completed' then return jsonb_build_object('id',r.id,'season',r.season,'status',r.status,'idempotent',true,'recorded_pick_count',r.recorded_pick_count,'locked_asset_count',(select count(*) from public.draft_pick_assets where league_id=lid and draft_year=yr and asset_status in('consumed','historical')));end if;
 if r.expected_pick_count<=0 then raise exception 'draft_completion_expected_pick_count_invalid';end if;
 select count(*) into recorded from public.rookie_draft_board_assignments where league_id=lid and draft_year=yr;
 if recorded<>r.expected_pick_count then raise exception 'draft_completion_pick_count_mismatch';end if;
 select count(*) into team_count from public.league_teams where league_id=lid;
 if team_count<=0 or r.expected_pick_count%team_count<>0 then raise exception 'draft_completion_structure_invalid';end if;
 round_count:=r.expected_pick_count/team_count;
 select count(*) into asset_count from public.draft_pick_assets where league_id=lid and draft_year=yr;
 if asset_count<>r.expected_pick_count then raise exception 'draft_completion_asset_count_mismatch';end if;
 if exists(select 1 from public.draft_pick_assets where league_id=lid and draft_year=yr group by round_number,original_league_team_id having count(*)<>1)then raise exception 'draft_completion_asset_slot_duplicate';end if;
 if exists(select 1 from generate_series(1,round_count)g cross join public.league_teams t where t.league_id=lid and not exists(select 1 from public.draft_pick_assets a where a.league_id=lid and a.draft_year=yr and a.round_number=g and a.original_league_team_id=t.id))then raise exception 'draft_completion_asset_slot_missing';end if;
 if exists(select 1 from public.draft_pick_assets a left join public.league_teams o on o.id=a.original_league_team_id and o.league_id=lid left join public.league_teams c on c.id=a.current_owner_league_team_id and c.league_id=lid where a.league_id=lid and a.draft_year=yr and(o.id is null or c.id is null))then raise exception 'draft_completion_asset_cross_league';end if;
 if exists(select 1 from public.draft_pick_assets a left join public.rookie_draft_board_assignments b on b.id=a.rookie_draft_assignment_id and b.league_id=lid and b.draft_year=yr and b.draft_round=a.round_number and b.original_league_team_id=a.current_owner_league_team_id where a.league_id=lid and a.draft_year=yr and b.id is null)then raise exception 'draft_completion_assignment_asset_mismatch';end if;
 if exists(select 1 from public.rookie_draft_board_assignments b left join public.draft_pick_assets a on a.rookie_draft_assignment_id=b.id and a.league_id=lid and a.draft_year=yr where b.league_id=lid and b.draft_year=yr and a.stable_pick_id is null)then raise exception 'draft_completion_assignment_without_asset';end if;
 update public.draft_pick_assets set asset_status='historical' where league_id=lid and draft_year=yr and asset_status='tradable';get diagnostics locked_count=row_count;
 update public.league_draft_lifecycles set status='completed',recorded_pick_count=recorded,completed_at=clock_timestamp(),completed_by=actor,updated_at=clock_timestamp() where id=r.id returning * into r;
 return jsonb_build_object('id',r.id,'season',r.season,'status',r.status,'idempotent',false,'recorded_pick_count',recorded,'locked_asset_count',locked_count);
end$$;

create or replace function public.execute_canonical_trade_authenticated(p_request jsonb) returns jsonb
language plpgsql security definer set search_path=pg_catalog,public as $$
declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;idem text:=nullif(btrim(p_request->>'idempotency_key'),'');notes text:=nullif(btrim(p_request->>'notes'),'');participants jsonb:=p_request->'participant_team_ids';players jsonb:=coalesce(p_request->'player_movements','[]');picks jsonb:=coalesce(p_request->'draft_pick_movements','[]');retentions jsonb:=coalesce(p_request->'retained_salary','[]');fp text;trade public.canonical_trades%rowtype;item jsonb;season_item jsonb;agreement public.contract_agreements%rowtype;pick public.draft_pick_assets%rowtype;obligation public.contract_seasons%rowtype;from_id uuid;to_id uuid;v_pick_id uuid;v_contract_id uuid;amount numeric;yr int;movement_count int:=0;retained_count int:=0;
begin
 perform public.require_commissioner_authority(lid);
 if idem is null or jsonb_typeof(participants)<>'array' or jsonb_array_length(participants) not between 2 and 4 or jsonb_typeof(players)<>'array' or jsonb_typeof(picks)<>'array' or jsonb_typeof(retentions)<>'array' then raise exception 'trade_request_invalid';end if;
 if(select count(distinct value) from jsonb_array_elements_text(participants))<>jsonb_array_length(participants) then raise exception 'trade_participants_duplicate';end if;
 if exists(select 1 from jsonb_array_elements_text(participants)p(value) left join public.league_teams t on t.id=p.value::uuid and t.league_id=lid where t.id is null) then raise exception 'trade_participant_not_in_league';end if;
 fp:=encode(extensions.digest(convert_to((p_request-'notes')::text,'UTF8'),'sha256'),'hex');
 perform pg_advisory_xact_lock(hashtextextended('canonical-trade:'||lid::text||':'||idem,0));
 select * into trade from public.canonical_trades where league_id=lid and idempotency_key=idem;
 if trade.id is not null then if trade.request_fingerprint<>fp then raise exception 'trade_idempotency_conflict';end if;return jsonb_build_object('trade_id',trade.id,'status',trade.status,'idempotent',true);end if;
 if exists(select 1 from jsonb_array_elements(players||picks)x group by coalesce(x->>'contract_id',x->>'draft_pick_id') having count(*)>1) then raise exception 'trade_asset_duplicated';end if;
 insert into public.canonical_trades(league_id,status,idempotency_key,request_fingerprint,notes,created_by)values(lid,'completed',idem,fp,notes,actor)returning * into trade;
 insert into public.canonical_trade_participants(trade_id,league_id,league_team_id)select trade.id,lid,value::uuid from jsonb_array_elements_text(participants);
 for item in select value from jsonb_array_elements(players)loop
  v_contract_id:=(item->>'contract_id')::uuid;from_id:=(item->>'from_league_team_id')::uuid;to_id:=(item->>'to_league_team_id')::uuid;
  if from_id=to_id or not(participants ? from_id::text)or not(participants ? to_id::text)then raise exception 'trade_player_participant_invalid';end if;
  select * into agreement from public.contract_agreements where id=v_contract_id and league_id=lid for update;
  if agreement.id is null or agreement.league_team_id<>from_id or agreement.status not in('active','scheduled') or agreement.superseded_by_contract_id is not null then raise exception 'trade_player_ownership_invalid';end if;
  insert into public.canonical_trade_asset_movements(trade_id,league_id,asset_type,contract_agreement_id,player_id,from_league_team_id,to_league_team_id,asset_snapshot)values(trade.id,lid,'player',agreement.id,agreement.player_id,from_id,to_id,to_jsonb(agreement)-'updated_at');
  update public.contract_agreements set league_team_id=to_id,updated_at=clock_timestamp() where id=agreement.id;
  update public.contract_seasons set league_team_id=to_id,updated_at=clock_timestamp() where contract_id=agreement.id and obligation_status in('active','scheduled');
  insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)values(agreement.id,lid,to_id,agreement.player_id,'traded',(select season from public.league_seasons where league_id=lid and is_active order by season desc limit 1),'canonical_trade',actor,jsonb_build_object('league_team_id',from_id),jsonb_build_object('league_team_id',to_id),jsonb_build_object('trade_id',trade.id),format('trade:%s:player:%s',trade.id,agreement.id));
  movement_count:=movement_count+1;
 end loop;
 for item in select value from jsonb_array_elements(picks)loop
  v_pick_id:=(item->>'draft_pick_id')::uuid;from_id:=(item->>'from_league_team_id')::uuid;to_id:=(item->>'to_league_team_id')::uuid;
  if from_id=to_id or not(participants ? from_id::text)or not(participants ? to_id::text)then raise exception 'trade_pick_participant_invalid';end if;
  select * into pick from public.draft_pick_assets where stable_pick_id=v_pick_id and league_id=lid for update;
  if pick.stable_pick_id is null or pick.current_owner_league_team_id<>from_id or pick.asset_status<>'tradable' then raise exception 'trade_pick_ownership_or_status_invalid';end if;
  if not exists(select 1 from public.league_draft_lifecycles d where d.league_id=lid and d.season=pick.draft_year and d.status in('scheduled','in_progress'))then raise exception 'trade_pick_lifecycle_not_tradeable';end if;
  insert into public.canonical_trade_asset_movements(trade_id,league_id,asset_type,draft_pick_id,from_league_team_id,to_league_team_id,asset_snapshot)values(trade.id,lid,'draft_pick',pick.stable_pick_id,from_id,to_id,to_jsonb(pick));
  update public.draft_pick_assets set current_owner_league_team_id=to_id,trade_provenance=trade_provenance||jsonb_build_array(jsonb_build_object('trade_id',trade.id,'from',from_id,'to',to_id,'at',clock_timestamp()))where stable_pick_id=pick.stable_pick_id;
  movement_count:=movement_count+1;
 end loop;
 for item in select value from jsonb_array_elements(retentions)loop
  v_contract_id:=(item->>'contract_id')::uuid;from_id:=(item->>'retaining_league_team_id')::uuid;to_id:=(item->>'receiving_league_team_id')::uuid;
  if jsonb_typeof(item->'seasons')<>'array' or jsonb_array_length(item->'seasons') not between 1 and 4 then raise exception 'retained_salary_horizon_invalid';end if;
  if not exists(select 1 from public.canonical_trade_asset_movements m where m.trade_id=trade.id and m.contract_agreement_id=v_contract_id and m.from_league_team_id=from_id and m.to_league_team_id=to_id)then raise exception 'retained_salary_player_movement_missing';end if;
  for season_item in select value from jsonb_array_elements(item->'seasons')loop
   yr:=(season_item->>'season')::int;amount:=(season_item->>'amount')::numeric;if amount<=0 then raise exception 'retained_salary_amount_invalid';end if;
   select * into obligation from public.contract_seasons where contract_id=v_contract_id and league_id=lid and season=yr and obligation_status in('active','scheduled') for update;
   if obligation.id is null then raise exception 'retained_salary_contract_season_invalid';end if;
   if coalesce((select sum(r.amount)from public.trade_retained_salary_obligations r where r.contract_season_id=obligation.id and r.status='active'),0)+amount>obligation.cap_hit then raise exception 'retained_salary_exceeds_contract_cap_hit';end if;
   insert into public.trade_retained_salary_obligations(league_id,trade_id,player_id,contract_agreement_id,contract_season_id,retaining_league_team_id,receiving_league_team_id_at_creation,season,amount,status,reason,source,created_by)values(lid,trade.id,obligation.player_id,v_contract_id,obligation.id,from_id,to_id,yr,amount,'active','trade_retained_salary','canonical_trade_rpc',actor);retained_count:=retained_count+1;
  end loop;
 end loop;
 if movement_count=0 then raise exception 'trade_has_no_assets';end if;
 return jsonb_build_object('trade_id',trade.id,'status','completed','idempotent',false,'participant_count',jsonb_array_length(participants),'movement_count',movement_count,'retained_obligation_count',retained_count);
end$$;

revoke all on function public.configure_draft_lifecycle_authenticated(jsonb),public.initialize_draft_inventory_authenticated(jsonb),public.complete_rookie_draft_authenticated(jsonb),public.execute_canonical_trade_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.configure_draft_lifecycle_authenticated(jsonb),public.initialize_draft_inventory_authenticated(jsonb),public.complete_rookie_draft_authenticated(jsonb),public.execute_canonical_trade_authenticated(jsonb) to authenticated;

alter function public.read_canonical_team_state_authenticated(jsonb) rename to read_canonical_team_state_authenticated_pre_trade;
revoke all on function public.read_canonical_team_state_authenticated_pre_trade(jsonb) from public,anon,authenticated,service_role;

create function public.read_canonical_team_state_authenticated(p_request jsonb) returns jsonb
language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare base jsonb;lid uuid;yr int;scope_id uuid;
begin
 base:=public.read_canonical_team_state_authenticated_pre_trade(p_request);
 lid:=(base->>'league_id')::uuid;yr:=(base->>'season')::int;scope_id:=nullif(base->>'scope_team_id','')::uuid;
 return base||jsonb_build_object('retained_salary',coalesce((
  select jsonb_agg(to_jsonb(x)order by x.player_name,x.season,x.retaining_league_team_id)
  from(
   select r.id::text id,r.league_id::text league_id,r.trade_id::text trade_id,r.player_id,
    coalesce(p.player_name,r.player_id,'Player')player_name,r.contract_agreement_id::text contract_agreement_id,
    r.contract_season_id::text contract_season_id,r.retaining_league_team_id::text retaining_league_team_id,
    r.receiving_league_team_id_at_creation::text receiving_league_team_id_at_creation,r.season,r.amount,r.status,r.reason,r.source,r.created_at
   from public.trade_retained_salary_obligations r
   join public.contract_agreements a on a.id=r.contract_agreement_id and a.league_id=r.league_id
   left join public.player_universe p on p.sleeper_id=r.player_id
   where r.league_id=lid and r.season=yr and r.status='active'
    and(scope_id is null or r.retaining_league_team_id=scope_id or a.league_team_id=scope_id)
  )x
 ),'[]'::jsonb));
end$$;
revoke all on function public.read_canonical_team_state_authenticated(jsonb) from public,anon,authenticated,service_role;
grant execute on function public.read_canonical_team_state_authenticated(jsonb) to authenticated;

-- Carry retained responsibility through the existing deterministic rollover cap calculation.
do $$
declare d text;writer text;old_roster text;new_roster text;old_preserved text;new_preserved text;old_dead text;new_dead text;old_lock text;new_lock text;
begin
 select pg_get_functiondef('public.phase3b10b_derive_team_caps_private(uuid,uuid,uuid,numeric,text)'::regprocedure)into d;
 old_roster:=$patch$    select roster_row.league_team_id,season_row.salary::numeric as amount
    from public.season_roster_assignments roster_row
    join public.contract_seasons season_row on season_row.id=roster_row.target_contract_season_id
    where roster_row.assignment_set_id=assignment_set_row.id
      and season_row.obligation_status='active'$patch$;
 new_roster:=$patch$    select roster_row.league_team_id,(season_row.cap_hit-coalesce((select sum(retained_row.amount) from public.trade_retained_salary_obligations retained_row where retained_row.contract_season_id=season_row.id and retained_row.status='active'),0))::numeric as amount
    from public.season_roster_assignments roster_row
    join public.contract_seasons season_row on season_row.id=roster_row.target_contract_season_id
    where roster_row.assignment_set_id=assignment_set_row.id
      and season_row.obligation_status='active'$patch$;
 old_preserved:=$patch$    select agreement_row.league_team_id,season_row.cap_hit::numeric as amount
    from public.contract_agreements agreement_row$patch$;
 new_preserved:=$patch$    select agreement_row.league_team_id,(season_row.cap_hit-coalesce((select sum(retained_row.amount) from public.trade_retained_salary_obligations retained_row where retained_row.contract_season_id=season_row.id and retained_row.status='active'),0))::numeric as amount
    from public.contract_agreements agreement_row$patch$;
 old_dead:=$patch$  ), dead_by_team as (
    select dead_row.league_team_id,coalesce(sum(dead_row.amount),0) dead_cap,
           count(*)::integer dead_count
    from public.rollover_dead_cap_obligations dead_row
    where dead_row.rollover_execution_id=execution_row.id
    group by dead_row.league_team_id
  ), cap_rows as ($patch$;
 new_dead:=$patch$  ), dead_sources as materialized (
    select dead_row.league_team_id,dead_row.amount
    from public.rollover_dead_cap_obligations dead_row
    where dead_row.rollover_execution_id=execution_row.id
    union all
    select retained_row.retaining_league_team_id,retained_row.amount
    from public.trade_retained_salary_obligations retained_row
    join public.league_seasons target_season on target_season.id=assignment_set_row.target_league_season_id
    where retained_row.league_id=execution_row.league_id and retained_row.season=target_season.season and retained_row.status='active'
  ), dead_by_team as (
    select dead_row.league_team_id,coalesce(sum(dead_row.amount),0) dead_cap,
           count(*)::integer dead_count
    from dead_sources dead_row
    group by dead_row.league_team_id
  ), cap_rows as ($patch$;
 if length(d)-length(replace(d,old_roster,''))<>length(old_roster)or length(d)-length(replace(d,old_preserved,''))<>length(old_preserved)or length(d)-length(replace(d,old_dead,''))<>length(old_dead)then raise exception 'retained_salary_rollover_cap_patch_shape_mismatch';end if;
 d:=replace(d,old_roster,new_roster);d:=replace(d,old_preserved,new_preserved);d:=replace(d,old_dead,new_dead);
 execute d;
 select pg_get_functiondef('public.write_prepared_caps_phase3b10b_private(uuid,uuid,uuid,uuid)'::regprocedure)into writer;
 old_lock:=$patch$  perform 1 from public.rollover_dead_cap_obligations where rollover_execution_id=execution_row.id order by league_team_id,contract_agreement_id for share;$patch$;
 new_lock:=old_lock||$patch$
  perform 1 from public.trade_retained_salary_obligations where league_id=execution_row.league_id and status='active' order by retaining_league_team_id,contract_agreement_id,season for share;$patch$;
 if length(writer)-length(replace(writer,old_lock,''))<>length(old_lock)then raise exception 'retained_salary_rollover_lock_patch_shape_mismatch';end if;
 writer:=replace(writer,old_lock,new_lock);execute writer;
end$$;
