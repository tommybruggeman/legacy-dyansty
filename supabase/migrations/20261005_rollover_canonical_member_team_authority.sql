begin;

-- Canonical team authority is a member membership linked to exactly one team.
-- Actor identity is always auth.uid(); neither league nor team comes from the
-- authenticated request payload.
create or replace function public.require_team_decision_authority(p_decision_id uuid)
returns text
language plpgsql
stable
security definer
set search_path=pg_catalog,public
as $$
declare
  actor uuid := public.require_authenticated_user();
  decision_row public.rollover_owner_decisions%rowtype;
  membership_count integer;
  resolved_role text;
  resolved_team_id uuid;
begin
  select * into decision_row
    from public.rollover_owner_decisions
   where id=p_decision_id;
  if decision_row.id is null then
    raise exception 'Owner decision not found';
  end if;

  select count(*),
         (array_agg(lower(m.role) order by m.id))[1],
         (array_agg(m.league_team_id order by m.id))[1]
    into membership_count,resolved_role,resolved_team_id
    from public.league_memberships m
   where m.league_id=decision_row.league_id
     and m.user_id=actor
     and lower(m.role)='member';

  if membership_count<>1
     or resolved_role<>'member'
     or resolved_team_id is null
     or resolved_team_id<>decision_row.league_team_id
     or not exists(
       select 1 from public.league_teams t
        where t.id=resolved_team_id
          and t.league_id=decision_row.league_id
     ) then
    raise exception 'Canonical member decision authority required for linked team';
  end if;
  return 'member';
end
$$;

-- This implementation remains non-executable by client roles.  It repeats the
-- canonical membership check as defense in depth because both the authenticated
-- member wrapper and the separately authorized commissioner override call it.
create or replace function public.submit_rollover_owner_decision(p_request jsonb)
returns jsonb
language plpgsql
security definer
set search_path=pg_catalog,public
as $$
declare
  x public.rollover_executions%rowtype;
  d public.rollover_owner_decisions%rowtype;
  prior record;
  k text;
  choice text;
  next_status text;
  rev int;
  membership_count integer;
  resolved_role text;
  resolved_team_id uuid;
begin
  k=nullif(p_request->>'idempotency_key','');choice=p_request->>'choice';
  if k is null then raise exception 'idempotency_key required'; end if;
  select * into prior from public.rollover_owner_decision_revisions where idempotency_key=k;
  if found then
    if prior.owner_decision_id<>(p_request->>'owner_decision_id')::uuid or prior.new_choice is distinct from choice then raise exception 'Idempotency key payload conflict'; end if;
    select * into d from public.rollover_owner_decisions where id=prior.owner_decision_id;
    return jsonb_build_object('idempotent',true,'decision',to_jsonb(d),'revision',to_jsonb(prior));
  end if;
  select * into d from public.rollover_owner_decisions where id=(p_request->>'owner_decision_id')::uuid for update;
  select * into x from public.rollover_executions where id=d.rollover_execution_id for share;
  if x.id is null or x.status<>'decision_window_open' or now()>=d.deadline or d.locked_at is not null or d.execution_status in ('executing','executed','cancelled') then raise exception 'Decision window is not mutable'; end if;
  if nullif(p_request->>'expected_decision_fingerprint','') is null or d.metadata->>'decision_fingerprint' is distinct from p_request->>'expected_decision_fingerprint' then raise exception 'Stale owner decision fingerprint'; end if;

  select count(*),
         (array_agg(lower(m.role) order by m.id))[1],
         (array_agg(m.league_team_id order by m.id))[1]
    into membership_count,resolved_role,resolved_team_id
    from public.league_memberships m
   where m.league_id=d.league_id
     and m.user_id=(p_request->>'submitted_by')::uuid;
  if membership_count<>1 then raise exception 'Canonical membership is missing or ambiguous'; end if;
  if resolved_role='member' then
    if resolved_team_id is null
       or resolved_team_id<>d.league_team_id
       or not exists(select 1 from public.league_teams t where t.id=resolved_team_id and t.league_id=d.league_id) then
      raise exception 'Owner authorization failed';
    end if;
  elsif resolved_role not in ('commissioner','admin','host') then
    raise exception 'Owner authorization failed';
  end if;

  select coalesce(max(revision_number),0)+1 into rev from public.rollover_owner_decision_revisions where owner_decision_id=d.id;
  if rev-1<>(p_request->>'expected_revision_number')::int then raise exception 'Stale owner decision revision'; end if;
  next_status=case choice when 'recontract' then 'recontract_submitted' when 'decline' then 'decline_submitted' when 'commissioner_review' then 'commissioner_review_requested' else null end;
  if next_status is null then raise exception 'Unsupported owner choice'; end if;
  if choice='recontract' and (nullif(p_request->>'recontract_agreement_id','') is null or nullif(p_request->>'recontract_event_id','') is null) then raise exception 'Recontract normalized references required'; end if;
  if choice<>'recontract' and (nullif(p_request->>'recontract_agreement_id','') is not null or nullif(p_request->>'recontract_event_id','') is not null) then raise exception 'Non-recontract choice cannot carry recontract references'; end if;
  insert into public.rollover_owner_decision_revisions(owner_decision_id,rollover_execution_id,revision_number,prior_status,new_status,prior_choice,new_choice,changed_by,reason,evidence,request_id,idempotency_key)
  values(d.id,x.id,rev,d.decision_status,next_status,d.owner_choice,choice,(p_request->>'submitted_by')::uuid,p_request->>'reason',coalesce(p_request->'evidence','{}'),p_request->>'request_id',k);
  update public.rollover_owner_decisions set decision_status=next_status,owner_choice=choice,submitted_by=(p_request->>'submitted_by')::uuid,submitted_at=now(),recontract_agreement_id=case when choice='recontract' then (p_request->>'recontract_agreement_id')::uuid else null end,recontract_event_id=case when choice='recontract' then (p_request->>'recontract_event_id')::uuid else null end,metadata=metadata||jsonb_build_object('decision_fingerprint',p_request->>'decision_fingerprint','revision_number',rev) where id=d.id returning * into d;
  if choice='commissioner_review' then insert into public.rollover_commissioner_reviews(rollover_execution_id,league_id,source_season,target_season,player_id,agreement_id,league_team_id,review_type,review_status,execution_status,evidence,metadata) values(x.id,d.league_id,d.source_season,d.target_season,d.player_id,d.agreement_id,d.league_team_id,'owner_escalation','review_required','pending',coalesce(p_request->'evidence','{}'),jsonb_build_object('owner_decision_id',d.id)) on conflict(rollover_execution_id,player_id,review_type) do nothing; end if;
  return jsonb_build_object('idempotent',false,'decision',to_jsonb(d),'revision_number',rev);
end
$$;

revoke all on function public.require_team_decision_authority(uuid) from public,anon,authenticated,service_role;
revoke all on function public.submit_rollover_owner_decision(jsonb) from public,anon,authenticated,service_role;

comment on function public.require_team_decision_authority(uuid) is
  'Private canonical member-team authority: auth.uid(), exactly one member membership, server-resolved league_team_id.';
comment on function public.submit_rollover_owner_decision(jsonb) is
  'Private owner-decision mutation used only after authenticated member or separate commissioner authorization.';

commit;
