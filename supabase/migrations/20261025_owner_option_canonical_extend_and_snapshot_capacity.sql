begin;

-- 20261020 narrows the owner population to canonical rookie options. Preserve
-- the already-certified Phase-B v3 positional fingerprint contract while
-- changing only the classification/population query.
create or replace function public.phaseb_owner_expected_cases_private(p_execution_id uuid)
returns table(case_key text,case_fingerprint text,case_payload jsonb)
language sql security definer set search_path=pg_catalog,public stable as $$
 with cases as(
  select x.source_season,x.target_season,x.league_id,a.id agreement_id,a.player_id,a.league_team_id,
   a.status agreement_status,case when r.roster_designation in('taxi','ir') then r.roster_designation else 'rostered' end roster_designation,
   r.sleeper_player_id,case when cs.salary is null then null else to_char(cs.salary,'FM9999999999999999999999999990.00') end source_salary,
   greatest(a.end_season-x.source_season,0) source_contract_years
  from public.rollover_executions x
  join public.contract_rollover_classifications c on c.league_id=x.league_id and c.source_season=x.source_season
   and c.target_season=x.target_season and c.classification='rookie_option_eligible'
  join public.contract_agreements a on a.id=c.contract_agreement_id
  join public.league_seasons s on s.league_id=x.league_id and s.season=x.source_season
  join public.season_roster_assignments r on r.league_season_id=s.id and r.league_team_id=a.league_team_id
   and r.sleeper_player_id=a.player_id
  left join public.contract_seasons cs on cs.contract_id=a.id and cs.season=x.source_season
  where x.id=p_execution_id
 ),payloads as(
  select *,jsonb_build_object('classification','ROOKIE_OPTION_ELIGIBLE','league_id',league_id,
   'source_season',source_season,'target_season',target_season,'agreement_id',agreement_id,
   'player_id',player_id,'league_team_id',league_team_id,'agreement_status',agreement_status,
   'roster_designation',roster_designation,'sleeper_player_id',sleeper_player_id,
   'source_salary',source_salary,'source_contract_years',source_contract_years,
   'rostered_status','rostered','roster_slot',roster_designation) payload from cases)
 select format('%s:%s:%s:%s:%s',source_season,target_season,agreement_id,player_id,league_team_id),
  public.phaseb_owner_case_fingerprint_v3_private(payload),payload from payloads
 order by agreement_id,player_id,league_team_id
$$;
revoke all on function public.phaseb_owner_expected_cases_private(uuid)
 from public,anon,authenticated,service_role;

-- The Phase 3B.7A snapshot row type exposes guaranteed_salary. The original
-- third-round branch referenced the resolution-table column name before the
-- resolution row existed, which only surfaced when a canonical R3 EXTEND ran.
do $$
declare definition text;signature regprocedure :=
 'public.execute_rollover_typed_handler_phase3b7a_private(jsonb,uuid,uuid,uuid,uuid)'::regprocedure;
begin
 select pg_get_functiondef(signature) into definition;
 if definition not like '%c.frozen_guaranteed_salary<>1%' then
  raise exception 'Expected Phase 3B.7A guaranteed-salary reference not found';
 end if;
 execute replace(definition,'c.frozen_guaranteed_salary<>1','c.guaranteed_salary<>1');
end $$;

-- 20261020 added the continuing-contract loop to Phase 3B.7B using alias c,
-- which conflicts with the handler's existing PL/pgSQL snapshot-case record c.
do $$
declare definition text;signature regprocedure :=
 'public.execute_rollover_typed_handler_phase3b7b_private(jsonb,uuid,uuid,uuid,uuid)'::regprocedure;
begin
 select pg_get_functiondef(signature) into definition;
 if definition not like '%select c.contract_agreement_id from public.contract_rollover_classifications c where c.league_id=snap.league_id%' then
  raise exception 'Expected Phase 3B.7B continuing-contract alias not found';
 end if;
 definition:=replace(definition,
  'select c.contract_agreement_id from public.contract_rollover_classifications c where c.league_id=snap.league_id',
  'select cls.contract_agreement_id from public.contract_rollover_classifications cls where cls.league_id=snap.league_id');
 definition:=replace(definition,
  'and c.source_season=(select source_season from public.rollover_executions where id=p_rollover_execution_id) and c.target_season=(select target_season from public.rollover_executions where id=p_rollover_execution_id) and c.classification in(',
  'and cls.source_season=(select source_season from public.rollover_executions where id=p_rollover_execution_id) and cls.target_season=(select target_season from public.rollover_executions where id=p_rollover_execution_id) and cls.classification in(');
 definition:=replace(definition,'order by c.contract_agreement_id loop','order by cls.contract_agreement_id loop');
 execute definition;
end $$;

-- The ordinary-expiration source added by 20261020 reused the Phase 3B.7C
-- handler's agreement-record name a as a SQL alias, making a.id ambiguous.
do $$
declare definition text;signature regprocedure :=
 'public.execute_rollover_typed_handler_phase3b7c_private(jsonb,uuid,uuid,uuid,uuid)'::regprocedure;
begin
 select pg_get_functiondef(signature) into definition;
 if definition not like '%select c.*,a.status agreement_status,a.league_team_id from public.contract_rollover_classifications c join public.contract_agreements a on a.id=c.contract_agreement_id%' then
  raise exception 'Expected Phase 3B.7C ordinary-expiration aliases not found';
 end if;
 definition:=replace(definition,
  'select c.*,a.status agreement_status,a.league_team_id from public.contract_rollover_classifications c join public.contract_agreements a on a.id=c.contract_agreement_id where c.league_id=x.league_id and c.source_season=x.source_season and c.target_season=x.target_season and c.classification=''ordinary_expiration'' order by c.player_id,c.contract_agreement_id',
  'select cls.*,agreement_row.status agreement_status,agreement_row.league_team_id from public.contract_rollover_classifications cls join public.contract_agreements agreement_row on agreement_row.id=cls.contract_agreement_id where cls.league_id=x.league_id and cls.source_season=x.source_season and cls.target_season=x.target_season and cls.classification=''ordinary_expiration'' order by cls.player_id,cls.contract_agreement_id');
 execute definition;
end $$;

-- Preserved off-roster liabilities are included in continuing and represented
-- separately as intentional exclusions. Subtract them from assigned so the
-- target-roster completeness equation and row material do not double-count.
do $$
declare definition text;signature regprocedure :=
 'public.write_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid,uuid)'::regprocedure;
begin
 select pg_get_functiondef(signature) into definition;
 if definition not like '%assigned:=continuing;%' then
  raise exception 'Expected Phase 3B.8A assigned population expression not found';
 end if;
 execute replace(definition,'assigned:=continuing;','assigned:=continuing-intentional;');
end $$;

-- Prepared expiring-contract validation must respect the same intentional
-- preserved-off-roster exclusion certified by target-roster preparation.
do $$
declare definition text;signature regprocedure :=
 'public.write_prepared_expiring_phase3b10d_private(uuid,uuid,uuid,uuid)'::regprocedure;
 old_fragment text:='where a.status=''active'' and cs.obligation_status=''active'' and ra.id is null) then perform public.raise_phase3b6c1_failure(''expiring_target_assignment_missing''';
 new_fragment text:='where a.status=''active'' and cs.obligation_status=''active'' and ra.id is null and not public.phase3b8a_is_preserved_off_roster_liability(snap.id,a.id,a.player_id,a.league_team_id)) then perform public.raise_phase3b6c1_failure(''expiring_target_assignment_missing''';
begin
 select pg_get_functiondef(signature) into definition;
 if definition not like '%'||old_fragment||'%' then
  raise exception 'Expected Phase 3B.10D target-assignment check not found';
 end if;
 execute replace(definition,old_fragment,new_fragment);
end $$;

-- Rookie-option exercise is a decision against existing canonical option
-- authority.  A future exercise event is created only by operation 11.
alter table public.rollover_owner_decisions
  drop constraint if exists rollover_owner_decisions_check1;

create or replace function public.validate_rollover_rookie_option_extend_private(p_decision_id uuid)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare d public.rollover_owner_decisions%rowtype;a public.contract_agreements%rowtype;
 opt public.contract_seasons%rowtype;round_no integer;expected_salary numeric;
begin
 select * into d from public.rollover_owner_decisions where id=p_decision_id for update;
 select * into a from public.contract_agreements where id=d.agreement_id for share;
 select * into opt from public.contract_seasons where contract_id=d.agreement_id
  and season=d.target_season and is_option_year for share;
 select draft_round into round_no from public.player_universe where sleeper_id=d.player_id;
 expected_salary:=case round_no when 1 then 25 when 2 then 15 when 3 then 7 else null end;
 if d.id is null or a.id is null or a.league_id<>d.league_id
    or a.league_team_id<>d.league_team_id or a.player_id<>d.player_id
    or a.contract_type<>'rookie' or round_no not between 1 and 3
    or not exists(select 1 from public.contract_rollover_classifications c
      join public.rookie_draft_board_assignments b on b.id=c.rookie_draft_assignment_id
      where c.league_id=d.league_id and c.source_season=d.source_season
       and c.target_season=d.target_season and c.contract_agreement_id=d.agreement_id
       and c.player_id=d.player_id and c.classification='rookie_option_eligible'
       and not c.option_consumed and not b.option_consumed
       and b.draft_round=round_no and b.one_time_option_salary=expected_salary
       and b.one_time_option_term=1)
    or opt.id is null or opt.obligation_status<>'scheduled'
    or opt.salary is distinct from expected_salary
    or (select count(*) from public.contract_seasons s where s.contract_id=a.id
         and s.season>=d.target_season and s.is_option_year)<>1
    or (round_no=3 and opt.guaranteed_salary is distinct from 1)
    or lower(coalesce(d.initial_roster_slot,d.initial_roster_status,''))='taxi'
    or exists(select 1 from public.contract_events e where e.contract_id=a.id and e.event_type='option_exercised')
 then raise exception 'Canonical eligible unexercised rookie option authority required'; end if;
 return jsonb_build_object('agreement_id',a.id,'option_contract_season_id',opt.id,
  'salary',opt.salary,'term',1,'draft_round',round_no,'authority','canonical_unexercised_rookie_option');
end $$;

create or replace function public.validate_rollover_owner_decision() returns trigger
language plpgsql set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;ok boolean:=false;
begin
 select * into x from public.rollover_executions where id=new.rollover_execution_id;
 if x.id is null or x.league_id<>new.league_id or x.source_season<>new.source_season or x.target_season<>new.target_season then raise exception 'Owner decision boundary mismatch.';end if;
 if new.deadline is distinct from x.owner_deadline then raise exception 'Owner decision deadline must match parent execution.';end if;
 if new.decision_status='no_response' and(x.owner_deadline is null or now()<x.owner_deadline) then raise exception 'No-response cannot be assigned before deadline.';end if;
 if tg_op='UPDATE' then
  if old.decision_status in('executed_retained','commissioner_hold') then raise exception 'Executed owner outcome is immutable.';end if;
  if old.decision_status='commissioner_review_requested' and new.decision_status='no_response' then raise exception 'Commissioner review cannot become no-response.';end if;
  ok:=new.decision_status=old.decision_status
   or(old.decision_status='waiting_for_owner' and new.decision_status in('recontract_submitted','recontract_validated','decline_submitted','commissioner_review_requested','no_response','cancelled'))
   or(old.decision_status='recontract_submitted' and new.decision_status in('recontract_invalid','recontract_validated','cancelled'))
   or(old.decision_status='recontract_invalid' and new.decision_status in('waiting_for_owner','blocked','cancelled'))
   or(old.decision_status='recontract_validated' and new.decision_status in('planned_retention','blocked','cancelled'))
   or(old.decision_status='decline_submitted' and new.decision_status in('planned_release','cancelled'))
   or(old.decision_status='no_response' and new.decision_status='planned_release')
   or(old.decision_status in('planned_retention','planned_release') and new.decision_status in('execution_ready','blocked','cancelled'))
   or(old.decision_status='execution_ready' and new.decision_status in('executed_retained','executed_released','blocked'))
   or(old.decision_status='executed_released' and new.decision_status='commissioner_hold')
   or(old.decision_status='blocked' and new.decision_status in('execution_ready','cancelled'));
  if not ok then raise exception 'Illegal owner-decision transition: % -> %',old.decision_status,new.decision_status;end if;
 end if;new.updated_at=now();return new;
end $$;

create or replace function public.submit_rollover_owner_decision(p_request jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$
declare x public.rollover_executions%rowtype;d public.rollover_owner_decisions%rowtype;prior record;k text;choice text;next_status text;rev int;membership_count integer;resolved_role text;resolved_team_id uuid;option_authority jsonb;
begin
 k=nullif(p_request->>'idempotency_key','');choice=p_request->>'choice';if k is null then raise exception 'idempotency_key required';end if;
 select * into prior from public.rollover_owner_decision_revisions where idempotency_key=k;
 if found then if prior.owner_decision_id<>(p_request->>'owner_decision_id')::uuid or prior.new_choice is distinct from choice then raise exception 'Idempotency key payload conflict';end if;select * into d from public.rollover_owner_decisions where id=prior.owner_decision_id;return jsonb_build_object('idempotent',true,'decision',to_jsonb(d),'revision',to_jsonb(prior));end if;
 select * into d from public.rollover_owner_decisions where id=(p_request->>'owner_decision_id')::uuid for update;select * into x from public.rollover_executions where id=d.rollover_execution_id for share;
 if x.id is null or x.status<>'decision_window_open' or now()>=d.deadline or d.locked_at is not null or d.execution_status in('executing','executed','cancelled') then raise exception 'Decision window is not mutable';end if;
 if nullif(p_request->>'expected_decision_fingerprint','') is null or d.metadata->>'decision_fingerprint' is distinct from p_request->>'expected_decision_fingerprint' then raise exception 'Stale owner decision fingerprint';end if;
 select count(*),(array_agg(lower(m.role) order by m.id))[1],(array_agg(m.league_team_id order by m.id))[1] into membership_count,resolved_role,resolved_team_id from public.league_memberships m where m.league_id=d.league_id and m.user_id=(p_request->>'submitted_by')::uuid;
 if membership_count<>1 then raise exception 'Canonical membership is missing or ambiguous';end if;
 if resolved_role='member' then if resolved_team_id is null or resolved_team_id<>d.league_team_id or not exists(select 1 from public.league_teams t where t.id=resolved_team_id and t.league_id=d.league_id) then raise exception 'Owner authorization failed';end if;elsif resolved_role not in('commissioner','admin','host') then raise exception 'Owner authorization failed';end if;
 select coalesce(max(revision_number),0)+1 into rev from public.rollover_owner_decision_revisions where owner_decision_id=d.id;if rev-1<>(p_request->>'expected_revision_number')::int then raise exception 'Stale owner decision revision';end if;
 next_status=case choice when 'recontract' then 'recontract_validated' when 'decline' then 'decline_submitted' when 'commissioner_review' then 'commissioner_review_requested' else null end;if next_status is null then raise exception 'Unsupported owner choice';end if;
 if choice='recontract' then
  if p_request?'recontract_agreement_id' or p_request?'recontract_event_id' then raise exception 'Caller-authored recontract references forbidden';end if;
  option_authority:=public.validate_rollover_rookie_option_extend_private(d.id);
 elsif p_request?'recontract_agreement_id' or p_request?'recontract_event_id' then raise exception 'Non-recontract choice cannot carry recontract references';end if;
 insert into public.rollover_owner_decision_revisions(owner_decision_id,rollover_execution_id,revision_number,prior_status,new_status,prior_choice,new_choice,changed_by,reason,evidence,request_id,idempotency_key) values(d.id,x.id,rev,d.decision_status,next_status,d.owner_choice,choice,(p_request->>'submitted_by')::uuid,p_request->>'reason',coalesce(p_request->'evidence','{}')||case when choice='recontract' then jsonb_build_object('canonical_option_authority',option_authority) else '{}'::jsonb end,p_request->>'request_id',k);
 update public.rollover_owner_decisions set decision_status=next_status,owner_choice=choice,submitted_by=(p_request->>'submitted_by')::uuid,submitted_at=now(),recontract_agreement_id=null,recontract_event_id=null,metadata=metadata||jsonb_build_object('decision_fingerprint',p_request->>'decision_fingerprint','revision_number',rev)||case when choice='recontract' then jsonb_build_object('canonical_option_authority',option_authority) else '{}'::jsonb end where id=d.id returning * into d;
 if choice='commissioner_review' then insert into public.rollover_commissioner_reviews(rollover_execution_id,league_id,source_season,target_season,player_id,agreement_id,league_team_id,review_type,review_status,execution_status,evidence,metadata) values(x.id,d.league_id,d.source_season,d.target_season,d.player_id,d.agreement_id,d.league_team_id,'owner_escalation','review_required','pending',coalesce(p_request->'evidence','{}'),jsonb_build_object('owner_decision_id',d.id)) on conflict(rollover_execution_id,player_id,review_type) do nothing;end if;
 return jsonb_build_object('idempotent',false,'decision',to_jsonb(d),'revision_number',rev);
end $$;

-- Operation 5/6 readiness consumes the validated canonical decision state; it
-- must not resurrect the retired future-ID requirement.
do $$
declare definition text;signature regprocedure :=
 'public.execute_rollover_typed_handler_phase3b6b_private(jsonb,uuid,uuid)'::regprocedure;
begin
 select pg_get_functiondef(signature) into definition;
 if definition not like '%d.recontract_agreement_id is not null and d.recontract_event_id is not null%' then
  raise exception 'Expected certified recontract readiness predicate not found';
 end if;
 definition:=replace(definition,
  'and d.recontract_agreement_id is not null and d.recontract_event_id is not null',
  'and d.recontract_agreement_id is null and d.recontract_event_id is null and d.metadata?''canonical_option_authority''');
 execute definition;
end $$;

-- Snapshot v2 stores bounded relational rows after constructing its canonical
-- arrays.  108 legitimate responses exceed the obsolete 512 KiB staging bound;
-- use the already-certified snapshot-v3 64 MiB total bound. Completeness,
-- duplicate, cardinality, fingerprint, and credential checks remain unchanged.
do $$
declare definition text;signature regprocedure :=
 'public.execute_rollover_typed_handler_phase3b6c1_private(jsonb,uuid,uuid,uuid,uuid)'::regprocedure;
begin
 select pg_get_functiondef(signature) into definition;
 if definition not like '%payload_size>524288%' then
  raise exception 'Expected snapshot-v2 staging bound not found';
 end if;
 definition:=replace(definition,'payload_size>524288','payload_size>67108864');
 execute definition;
end $$;

revoke all on function public.validate_rollover_rookie_option_extend_private(uuid) from public,anon,authenticated,service_role;
comment on function public.validate_rollover_rookie_option_extend_private(uuid) is
 'Private validation of canonical unexercised rookie-option authority; creates no agreement, season, or event and is reached only through the authenticated owner-decision RPC.';

commit;
