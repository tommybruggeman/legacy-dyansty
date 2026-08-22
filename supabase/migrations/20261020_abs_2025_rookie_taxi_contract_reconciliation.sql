-- ABs Always Open: narrowly reconcile the validated 2025->2026 legacy contract
-- transition with commissioner-authoritative Rookie Draft Board and taxi evidence.
-- This migration does not activate or publish 2026 and does not alter roster ownership.
begin;
set local search_path = pg_catalog, public;

create table public.rookie_draft_board_assignments (
 id uuid primary key default gen_random_uuid(),
 league_id uuid not null references public.leagues(id),
 player_id text not null references public.player_universe(sleeper_id),
 original_league_team_id uuid references public.league_teams(id),
 draft_year integer not null,draft_round integer not null check(draft_round between 1 and 3),
 round_pick integer not null check(round_pick between 1 and 10),overall_pick integer not null,
 rookie_contract_provenance boolean not null check(rookie_contract_provenance),
 original_salary numeric(12,2) not null check(original_salary>=0),original_contract_term integer not null check(original_contract_term>0),
 one_time_option_salary numeric(12,2) not null check(one_time_option_salary>=0),one_time_option_term integer not null check(one_time_option_term=1),
 option_consumed boolean not null default false,
 source_type text not null check(source_type in('rookie_draft_board_assignment','commissioner_historical_reconciliation')),
 source_event jsonb not null check(jsonb_typeof(source_event)='object'),
 deterministic_fingerprint text not null check(deterministic_fingerprint~'^[0-9a-f]{64}$'),
 created_at timestamptz not null default clock_timestamp(),
 unique(league_id,draft_year,overall_pick),unique(league_id,draft_year,player_id),
 check(overall_pick=(draft_round-1)*10+round_pick)
);

create table public.rookie_taxi_assignments (
 id uuid primary key default gen_random_uuid(),league_id uuid not null references public.leagues(id),
 player_id text not null references public.player_universe(sleeper_id),league_team_id uuid not null references public.league_teams(id),
 league_season_id uuid not null references public.league_seasons(id),
 rookie_draft_assignment_id uuid not null references public.rookie_draft_board_assignments(id),
 source_roster_assignment_id uuid references public.season_roster_assignments(id),
 normal_annual_charge numeric(12,2) not null,taxi_charge numeric(12,2) not null,
 contract_year_consumed boolean not null check(not contract_year_consumed),locked boolean not null check(locked),
 unlock_target_season integer not null,unlocked_at timestamptz,
 evidence jsonb not null check(jsonb_typeof(evidence)='object'),
 deterministic_fingerprint text not null check(deterministic_fingerprint~'^[0-9a-f]{64}$'),
 created_at timestamptz not null default clock_timestamp(),unique(league_season_id,player_id),
 check(taxi_charge=round(normal_annual_charge*0.50,2)),check(unlocked_at is null)
);

create table public.contract_transition_reconciliations (
 id uuid primary key default gen_random_uuid(),league_id uuid not null references public.leagues(id),
 source_season integer not null,target_season integer not null,
 legacy_transition_id uuid not null unique references public.contract_transition_executions(id),
 reconciliation_key text not null unique,reconciliation_status text not null check(reconciliation_status in('applying','certified')),
 before_fingerprint text not null check(before_fingerprint~'^[0-9a-f]{64}$'),
 after_fingerprint text check(after_fingerprint~'^[0-9a-f]{64}$'),
 expected_counts jsonb not null,actual_counts jsonb not null default '{}'::jsonb,
 evidence jsonb not null check(jsonb_typeof(evidence)='object'),created_at timestamptz not null default clock_timestamp(),certified_at timestamptz,
 check(target_season=source_season+1)
);

create table public.contract_reconciliation_before_rows (
 id uuid primary key default gen_random_uuid(),reconciliation_id uuid not null references public.contract_transition_reconciliations(id),
 table_name text not null check(table_name in('contract_agreements','contract_seasons')),
 row_id uuid not null,before_row jsonb not null check(jsonb_typeof(before_row)='object'),
 before_fingerprint text not null check(before_fingerprint~'^[0-9a-f]{64}$'),created_at timestamptz not null default clock_timestamp(),
 unique(reconciliation_id,table_name,row_id)
);

create table public.contract_rollover_classifications (
 id uuid primary key default gen_random_uuid(),league_id uuid not null references public.leagues(id),
 source_season integer not null,target_season integer not null,
 contract_agreement_id uuid not null references public.contract_agreements(id),player_id text not null,
 classification text not null check(classification in('ordinary_continuing','ordinary_expiration','rookie_initial_continuing','rookie_initial_taxi_paused','rookie_option_eligible','rookie_option_consumed')),
 rookie_draft_assignment_id uuid references public.rookie_draft_board_assignments(id),taxi_assignment_id uuid references public.rookie_taxi_assignments(id),
 option_consumed boolean not null,classification_evidence jsonb not null,
 deterministic_fingerprint text not null check(deterministic_fingerprint~'^[0-9a-f]{64}$'),created_at timestamptz not null default clock_timestamp(),
 unique(league_id,source_season,target_season,contract_agreement_id),check(target_season=source_season+1),
 check((classification like 'rookie_%')=(rookie_draft_assignment_id is not null)),
 check((classification='rookie_initial_taxi_paused')=(taxi_assignment_id is not null))
);

do $$declare t text;begin foreach t in array array['rookie_draft_board_assignments','rookie_taxi_assignments','contract_transition_reconciliations','contract_reconciliation_before_rows','contract_rollover_classifications'] loop
 execute format('alter table public.%I enable row level security',t);
 execute format('revoke all on public.%I from public,anon,authenticated',t);
 execute format('grant select,insert,update on public.%I to service_role',t);
end loop;end$$;

create policy rookie_board_member_read on public.rookie_draft_board_assignments for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rookie_draft_board_assignments.league_id and m.user_id=auth.uid()));
create policy rookie_taxi_member_read on public.rookie_taxi_assignments for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=rookie_taxi_assignments.league_id and m.user_id=auth.uid()));
create policy contract_reconciliation_commissioner_read on public.contract_transition_reconciliations for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=contract_transition_reconciliations.league_id and m.user_id=auth.uid() and m.role='commissioner'));
create policy contract_classification_commissioner_read on public.contract_rollover_classifications for select to authenticated using(exists(select 1 from public.league_memberships m where m.league_id=contract_rollover_classifications.league_id and m.user_id=auth.uid() and m.role='commissioner'));

-- Exact production drift guard. Any mismatch aborts the entire transaction.
do $$declare lid constant uuid:='9838a0a1-97c6-4cab-bb88-af177317abfe';src uuid;dst uuid;tx public.contract_transition_executions%rowtype;begin
 perform pg_advisory_xact_lock(hashtextextended('abs-contract-reconciliation:'||lid||':2025:2026',0));
 select id into src from public.league_seasons where league_id=lid and season=2025 and status='active' and is_active;
 select id into dst from public.league_seasons where league_id=lid and season=2026 and status='scheduled' and not is_active;
 if src is distinct from '5071560b-a545-42fb-a580-8bec436a6281'::uuid or dst is distinct from 'f9bd5d03-fde8-43e5-ab19-5894cbd49326'::uuid then raise exception 'ABS_RECONCILIATION_SEASON_AUTHORITY_MISMATCH';end if;
 select * into tx from public.contract_transition_executions where id='cbcef849-5ba2-4b1a-88bc-b285f73b0740';
 if tx.id is null or tx.league_id<>lid or tx.source_season<>2025 or tx.target_season<>2026 or tx.status<>'validated'
  or tx.started_at<>'2026-07-29 17:26:27.867332+00'::timestamptz or tx.completed_at<>tx.started_at
  or tx.agreement_count<>211 or tx.continuing_count<>92 or tx.expiring_count<>119 or tx.satisfied_season_count<>211
  or tx.activated_season_count<>92 or tx.expired_agreement_count<>119 or tx.expiration_event_count<>119
  or tx.expected_source_fingerprint<>'d852eb7df1a819ff32a468fb44c73347384d3a998d2e37a22ef446be96e894c8'
  or tx.actual_source_fingerprint<>tx.expected_source_fingerprint or tx.plan_fingerprint<>'2b0df8a91fd3693fdbe7f42941ab325d8c588bcd1ac8806b4a7104c6894d2a4f' then raise exception 'ABS_RECONCILIATION_LEGACY_TRANSITION_MISMATCH';end if;
 if (select count(*) from public.contract_agreements where league_id=lid)<>211
  or (select count(*) from public.contract_agreements where league_id=lid and status='active')<>92
  or (select count(*) from public.contract_agreements where league_id=lid and status='expired')<>119
  or (select count(*) from public.contract_seasons where league_id=lid and season=2025 and obligation_status='satisfied')<>211
  or (select count(*) from public.contract_seasons where league_id=lid and season=2026 and obligation_status='active')<>92
  or (select count(*) from public.contract_seasons where league_id=lid and season=2027 and obligation_status='scheduled')<>32
  or (select count(*) from public.contract_events where league_id=lid and event_type='expired' and metadata->>'transition_key'=tx.transition_key)<>119
  then raise exception 'ABS_RECONCILIATION_CONTRACT_COUNTS_MISMATCH';end if;
 if exists(select 1 from public.rollover_executions where league_id=lid and source_season=2025 and target_season=2026)
  or exists(select 1 from public.contract_transition_reconciliations where league_id=lid and source_season=2025 and target_season=2026)
  or exists(select 1 from public.free_agent_publications where league_id=lid and season=2026 and publication_status='published')
  then raise exception 'ABS_RECONCILIATION_ALREADY_STARTED_OR_PUBLISHED';end if;
end$$;

-- Canonical identity rows. Historical rows with no provable original team retain
-- a NULL original team and explicit identity-only evidence; future writes fail
-- closed through the authenticated RPC below.
with board(player_id,draft_round,round_pick,salary,term,opt_salary) as(values
 ('12527',1,1,15,2,25),('12507',1,2,12,2,25),('12529',1,3,9,2,25),('12526',1,4,8,2,25),('12530',1,5,6,2,25),
 ('12517',1,6,5,2,25),('12522',1,7,4,2,25),('12501',1,8,4,2,25),('12489',1,9,4,2,25),('12504',1,10,4,2,25),
 ('12514',2,1,3,2,15),('12481',2,2,3,2,15),('12518',2,3,3,2,15),('12508',2,4,3,2,15),('12469',2,5,3,2,15),
 ('12509',2,6,3,2,15),('12484',2,7,3,2,15),('12510',2,8,3,2,15),('12476',2,9,3,2,15),('12519',2,10,3,2,15),
 ('12483',3,1,1,1,7),('12547',3,2,1,1,7),('12457',3,3,1,1,7),('12498',3,4,1,1,7),('12521',3,5,1,1,7),
 ('12524',3,6,1,1,7),('12490',3,7,1,1,7),('12487',3,8,1,1,7),('12512',3,9,1,1,7),('12497',3,10,1,1,7)
),material as(select b.*,coalesce(a.league_team_id,case b.player_id when '12530' then '4e4e6a4c-b57b-4724-a389-35279467fe89'::uuid when '12469' then '4e4e6a4c-b57b-4724-a389-35279467fe89'::uuid when '12457' then 'f989c7f4-47bf-4d0a-b6cb-5a529fb1e807'::uuid when '12487' then 'fc78cfec-bb51-4506-873d-6c62c7c917ec'::uuid when '12521' then 'f989c7f4-47bf-4d0a-b6cb-5a529fb1e807'::uuid when '12497' then 'e5837204-8b22-449e-a338-5ea511c3e44f'::uuid end) team_id from board b left join public.contract_agreements a on a.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and a.player_id=b.player_id)
insert into public.rookie_draft_board_assignments(league_id,player_id,original_league_team_id,draft_year,draft_round,round_pick,overall_pick,rookie_contract_provenance,original_salary,original_contract_term,one_time_option_salary,one_time_option_term,option_consumed,source_type,source_event,deterministic_fingerprint)
select '9838a0a1-97c6-4cab-bb88-af177317abfe',player_id,team_id,2025,draft_round,round_pick,(draft_round-1)*10+round_pick,true,salary,term,opt_salary,1,false,'commissioner_historical_reconciliation',jsonb_build_object('board','commissioner_authoritative_2025','identity_status','resolved','original_team_status',case when team_id is null then 'not_proven' else 'corroborated' end),public.rollover_material_fingerprint(jsonb_build_object('league','9838a0a1-97c6-4cab-bb88-af177317abfe','year',2025,'round',draft_round,'pick',round_pick,'player',player_id,'team',team_id,'salary',salary,'term',term,'option_salary',opt_salary,'option_term',1)) from material;

with taxi(player_id) as(values('12510'),('12522'),('12509'),('12508'),('12498'),('12476'),('12490'),('12519'),('12524'))
insert into public.rookie_taxi_assignments(league_id,player_id,league_team_id,league_season_id,rookie_draft_assignment_id,source_roster_assignment_id,normal_annual_charge,taxi_charge,contract_year_consumed,locked,unlock_target_season,evidence,deterministic_fingerprint)
select a.league_id,a.player_id,a.league_team_id,'5071560b-a545-42fb-a580-8bec436a6281',b.id,r.id,s.salary,round(s.salary*.5,2),false,true,2026,jsonb_build_object('source','commissioner_authoritative_2025_taxi_sheet','captured_roster_assignment',r.id is not null,'financial_rows_mutated',false),public.rollover_material_fingerprint(jsonb_build_object('league',a.league_id,'season',2025,'player',a.player_id,'team',a.league_team_id,'board',b.id,'normal_charge',s.salary,'taxi_charge',round(s.salary*.5,2),'consumed',false,'locked',true,'unlock',2026))
from taxi x join public.contract_agreements a on a.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and a.player_id=x.player_id join public.contract_seasons s on s.contract_id=a.id and s.season=2025 join public.rookie_draft_board_assignments b on b.league_id=a.league_id and b.player_id=a.player_id left join public.season_roster_assignments r on r.league_season_id='5071560b-a545-42fb-a580-8bec436a6281' and r.sleeper_player_id=a.player_id;

-- Classify the complete 211-agreement population before changing lifecycle state.
insert into public.contract_rollover_classifications(league_id,source_season,target_season,contract_agreement_id,player_id,classification,rookie_draft_assignment_id,taxi_assignment_id,option_consumed,classification_evidence,deterministic_fingerprint)
select a.league_id,2025,2026,a.id,a.player_id,
 case when t.id is not null then 'rookie_initial_taxi_paused' when b.id is not null and a.status='expired' then 'rookie_option_eligible' when b.id is not null then 'rookie_initial_continuing' when a.status='expired' then 'ordinary_expiration' else 'ordinary_continuing' end,
 b.id,t.id,coalesce(b.option_consumed,false),jsonb_build_object('agreement_status_before',a.status,'board_provenance',b.id is not null,'taxi_2025',t.id is not null,'first_rookie_board_year',2025),
 public.rollover_material_fingerprint(jsonb_build_object('agreement',a.id,'player',a.player_id,'status',a.status,'board',b.id,'taxi',t.id,'source',2025,'target',2026))
from public.contract_agreements a left join public.rookie_draft_board_assignments b on b.league_id=a.league_id and b.player_id=a.player_id left join public.rookie_taxi_assignments t on t.league_id=a.league_id and t.player_id=a.player_id where a.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe';

do $$begin
 if (select count(*) from public.rookie_draft_board_assignments where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and draft_year=2025)<>30
 or (select count(*) from public.rookie_taxi_assignments where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe')<>9
 or (select count(*) from public.contract_rollover_classifications where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe')<>211
 or (select count(*) from public.contract_rollover_classifications where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and classification='ordinary_continuing')<>74
 or (select count(*) from public.contract_rollover_classifications where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and classification='ordinary_expiration')<>113
 or (select count(*) from public.contract_rollover_classifications where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and classification='rookie_initial_continuing')<>12
 or (select count(*) from public.contract_rollover_classifications where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and classification='rookie_initial_taxi_paused')<>9
 or (select count(*) from public.contract_rollover_classifications where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and classification='rookie_option_eligible')<>3
 then raise exception 'ABS_RECONCILIATION_CLASSIFICATION_MISMATCH';end if;
end$$;

-- Fingerprint the exact before material and retain every row that will change.
with m as(select jsonb_build_object('agreements',(select jsonb_agg(to_jsonb(a)-'updated_at' order by a.id) from public.contract_agreements a where a.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe'),'seasons',(select jsonb_agg(to_jsonb(s)-'updated_at' order by s.id) from public.contract_seasons s where s.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe'),'transition',(select to_jsonb(t)-'updated_at' from public.contract_transition_executions t where t.id='cbcef849-5ba2-4b1a-88bc-b285f73b0740')) material)
insert into public.contract_transition_reconciliations(league_id,source_season,target_season,legacy_transition_id,reconciliation_key,reconciliation_status,before_fingerprint,expected_counts,evidence)
select '9838a0a1-97c6-4cab-bb88-af177317abfe',2025,2026,'cbcef849-5ba2-4b1a-88bc-b285f73b0740','abs:2025:2026:legacy-transition-reconciliation:v1','applying',public.rollover_material_fingerprint(material),jsonb_build_object('source_updates',211,'target_updates',92,'agreement_updates',9,'season_inserts',12,'board_rows',30,'taxi_rows',9,'classifications',211),jsonb_build_object('legacy_transition_preserved',true,'expiration_events_preserved',119,'roster_ownership_mutated',false,'publication_performed',false) from m;

insert into public.contract_reconciliation_before_rows(reconciliation_id,table_name,row_id,before_row,before_fingerprint)
select r.id,'contract_seasons',s.id,to_jsonb(s),public.rollover_material_fingerprint(to_jsonb(s)-'updated_at') from public.contract_transition_reconciliations r join public.contract_seasons s on s.league_id=r.league_id and(s.season=2025 or(s.season=2026 and s.obligation_status='active')) where r.reconciliation_key='abs:2025:2026:legacy-transition-reconciliation:v1';
insert into public.contract_reconciliation_before_rows(reconciliation_id,table_name,row_id,before_row,before_fingerprint)
select r.id,'contract_agreements',a.id,to_jsonb(a),public.rollover_material_fingerprint(to_jsonb(a)-'updated_at') from public.contract_transition_reconciliations r join public.contract_rollover_classifications c on c.league_id=r.league_id and c.classification='rookie_initial_taxi_paused' join public.contract_agreements a on a.id=c.contract_agreement_id where r.reconciliation_key='abs:2025:2026:legacy-transition-reconciliation:v1';

select set_config('app.contract_transition_execution','contract-transition-executor-v1',true);
update public.contract_seasons set obligation_status='active',updated_at=clock_timestamp() where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and season=2025 and obligation_status='satisfied';
update public.contract_seasons set obligation_status='scheduled',updated_at=clock_timestamp() where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and season=2026 and obligation_status='active';

-- Restore taxi-paused agreement life. Six R1/R2 contracts retain two unconsumed
-- years (2026 and 2027); three R3 contracts retain their one year in 2026.
update public.contract_agreements a set status='active',end_season=case when b.draft_round in(1,2) then 2027 else 2026 end,updated_at=clock_timestamp()
from public.contract_rollover_classifications c join public.rookie_draft_board_assignments b on b.id=c.rookie_draft_assignment_id where a.id=c.contract_agreement_id and c.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and c.classification='rookie_initial_taxi_paused';

insert into public.contract_seasons(contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,guaranteed_salary,cap_hit,roster_bonus,dead_cap_if_released,obligation_status,is_option_year,option_type,source,source_legacy_contract_id)
select a.id,'f9bd5d03-fde8-43e5-ab19-5894cbd49326',a.league_id,a.league_team_id,a.player_id,2026,b.original_salary,null,b.original_salary,null,null,'scheduled',false,null,'abs_2025_taxi_preserved_initial_contract_v1',a.source_legacy_contract_id
from public.contract_rollover_classifications c join public.contract_agreements a on a.id=c.contract_agreement_id join public.rookie_draft_board_assignments b on b.id=c.rookie_draft_assignment_id where c.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and c.classification='rookie_initial_taxi_paused' and not exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season=2026);

insert into public.contract_seasons(contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,guaranteed_salary,cap_hit,roster_bonus,dead_cap_if_released,obligation_status,is_option_year,option_type,source,source_legacy_contract_id)
select a.id,'e51d6815-709c-4a19-8568-6632bebe74a9',a.league_id,a.league_team_id,a.player_id,2027,b.original_salary,null,b.original_salary,null,null,'scheduled',false,null,'abs_2025_taxi_preserved_initial_contract_v1',a.source_legacy_contract_id
from public.contract_rollover_classifications c join public.contract_agreements a on a.id=c.contract_agreement_id join public.rookie_draft_board_assignments b on b.id=c.rookie_draft_assignment_id where c.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and c.classification='rookie_initial_taxi_paused' and b.draft_round in(1,2) and not exists(select 1 from public.contract_seasons s where s.contract_id=a.id and s.season=2027);

-- Available is not exercised: a scheduled option row carries deterministic
-- economics while the agreement remains expired until an affirmative outcome.
insert into public.contract_seasons(contract_id,league_season_id,league_id,league_team_id,player_id,season,salary,guaranteed_salary,cap_hit,roster_bonus,dead_cap_if_released,obligation_status,is_option_year,option_type,source,source_legacy_contract_id)
select a.id,'f9bd5d03-fde8-43e5-ab19-5894cbd49326',a.league_id,a.league_team_id,a.player_id,2026,b.one_time_option_salary,1,b.one_time_option_salary,null,null,'scheduled',true,'rookie_one_time_resign_option','abs_2025_rookie_option_prepared_v1',a.source_legacy_contract_id
from public.contract_rollover_classifications c join public.contract_agreements a on a.id=c.contract_agreement_id join public.rookie_draft_board_assignments b on b.id=c.rookie_draft_assignment_id where c.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and c.classification='rookie_option_eligible';

-- Production-equivalent canonical readiness uses only proven rookie options and
-- ignores only the exact certified reconciliation of the exact legacy ledger.
create or replace function public.rollover_contract_preflight_readiness_private(p_league_id uuid,p_source_season integer,p_target_season integer) returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public as $$declare agreement_count int;source_count int;prepared_option_count int;option_eligible_count int;active_target_count int;prior_transition_count int;ordinary_count int;taxi_count int;unresolved_count int;blockers jsonb:='[]';material jsonb;begin
 select count(*) into agreement_count from public.contract_agreements where league_id=p_league_id;
 select count(*) into source_count from public.contract_seasons where league_id=p_league_id and season=p_source_season;
 select count(*) into prepared_option_count from public.contract_rollover_classifications c join public.contract_seasons s on s.contract_id=c.contract_agreement_id and s.season=p_target_season and s.obligation_status='scheduled' and s.is_option_year and s.option_type is not null where c.league_id=p_league_id and c.source_season=p_source_season and c.target_season=p_target_season and c.classification='rookie_option_eligible';
 select count(*) into option_eligible_count from public.contract_rollover_classifications where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season and classification='rookie_option_eligible';
 select count(*) into ordinary_count from public.contract_rollover_classifications where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season and classification='ordinary_expiration';
 select count(*) into taxi_count from public.contract_rollover_classifications where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season and classification='rookie_initial_taxi_paused';
 select agreement_count-count(*) into unresolved_count from public.contract_rollover_classifications where league_id=p_league_id and source_season=p_source_season and target_season=p_target_season;
 select count(*) into active_target_count from public.contract_seasons where league_id=p_league_id and season=p_target_season and obligation_status='active';
 select count(*) into prior_transition_count from public.contract_transition_executions t where t.league_id=p_league_id and t.source_season=p_source_season and t.target_season=p_target_season and not exists(select 1 from public.contract_transition_reconciliations r where r.legacy_transition_id=t.id and r.reconciliation_status='certified');
 if p_target_season<>p_source_season+1 then blockers:=blockers||'"contract_season_boundary_invalid"';end if;if source_count<>agreement_count then blockers:=blockers||'"contract_source_obligation_population_mismatch"';end if;if prepared_option_count<>option_eligible_count then blockers:=blockers||'"prepared_target_option_population_mismatch"';end if;if active_target_count<>0 then blockers:=blockers||'"target_contract_authority_already_activated"';end if;if prior_transition_count<>0 then blockers:=blockers||'"prior_contract_transition_conflicts_with_rollover"';end if;if unresolved_count<>0 then blockers:=blockers||'"contract_provenance_unresolved"';end if;
 material:=jsonb_build_object('schema','rollover-contract-preflight-v2','league_id',p_league_id,'source_season',p_source_season,'target_season',p_target_season,'agreement_count',agreement_count,'source_count',source_count,'prepared_option_count',prepared_option_count,'option_eligible_count',option_eligible_count,'ordinary_expiration_count',ordinary_count,'taxi_paused_count',taxi_count,'active_target_count',active_target_count,'prior_transition_count',prior_transition_count,'unresolved_provenance_count',unresolved_count,'blockers',blockers);
 return material||jsonb_build_object('ready',jsonb_array_length(blockers)=0,'deterministic_fingerprint',public.rollover_material_fingerprint(material));end$$;

create or replace function public.phaseb_owner_expected_cases_private(p_execution_id uuid) returns table(case_key text,case_fingerprint text,case_payload jsonb) language sql security definer set search_path=pg_catalog,public stable as $$select format('%s:%s:%s:%s:%s',x.source_season,x.target_season,a.id,a.player_id,a.league_team_id),public.rollover_material_fingerprint(jsonb_build_object('schema','rollover-owner-case-v3','classification','ROOKIE_OPTION_ELIGIBLE','league_id',x.league_id,'source_season',x.source_season,'target_season',x.target_season,'agreement_id',a.id,'player_id',a.player_id,'league_team_id',a.league_team_id,'roster_designation',coalesce(r.roster_designation,'rostered'),'source_salary',to_char(cs.salary,'FM9999999990.00'))),jsonb_build_object('league_id',x.league_id,'source_season',x.source_season,'target_season',x.target_season,'agreement_id',a.id,'player_id',a.player_id,'league_team_id',a.league_team_id,'rostered_status','rostered','roster_slot',coalesce(r.roster_designation,'rostered'),'classification','ROOKIE_OPTION_ELIGIBLE') from public.rollover_executions x join public.contract_rollover_classifications c on c.league_id=x.league_id and c.source_season=x.source_season and c.target_season=x.target_season and c.classification='rookie_option_eligible' join public.contract_agreements a on a.id=c.contract_agreement_id join public.league_seasons s on s.league_id=x.league_id and s.season=x.source_season join public.season_roster_assignments r on r.league_season_id=s.id and r.league_team_id=a.league_team_id and r.sleeper_player_id=a.player_id join public.contract_seasons cs on cs.contract_id=a.id and cs.season=x.source_season where x.id=p_execution_id order by a.id$$;

-- An affirmative exercise consumes the board-derived option exactly once.
do $$declare d text;old_fragment text;new_fragment text;begin
 select pg_get_functiondef('public.execute_rollover_typed_handler_phase3b7b_private(jsonb,uuid,uuid,uuid,uuid)'::regprocedure) into d;
 old_fragment:=' c public.rollover_owner_option_snapshot_v2_cases%rowtype;f public.rollover_owner_option_final_outcomes%rowtype;';
 new_fragment:=' c public.rollover_owner_option_snapshot_v2_cases%rowtype;continuing_case record;f public.rollover_owner_option_final_outcomes%rowtype;';
 d:=replace(d,old_fragment,new_fragment);
 old_fragment:='update public.contract_seasons set obligation_status=''active'',salary=coalesce(f.final_proposed_salary,salary),cap_hit=coalesce(f.final_proposed_salary,cap_hit),guaranteed_salary=c.guaranteed_salary,rollover_operation_code=code,rollover_final_outcome_id=f.id,rollover_evidence_hash=event_fp,updated_at=clock_timestamp() where id=tgt.id;';
 new_fragment:=old_fragment||'
   update public.rookie_draft_board_assignments set option_consumed=true where league_id=a.league_id and player_id=a.player_id and not option_consumed;
   if not found then perform public.raise_phase3b6c1_failure(''rookie_option_already_consumed_or_provenance_missing'',''{}'');end if;';
 d:=replace(d,old_fragment,new_fragment);
 old_fragment:=' result_hash:=public.rollover_material_fingerprint(jsonb_build_object(''schema'',''phase3b7b-result-v1''';
 new_fragment:=' if code=''ADVANCE_CONTRACT_SEASON_OBLIGATIONS'' then
  for continuing_case in select c.contract_agreement_id from public.contract_rollover_classifications c where c.league_id=snap.league_id and c.source_season=(select source_season from public.rollover_executions where id=p_rollover_execution_id) and c.target_season=(select target_season from public.rollover_executions where id=p_rollover_execution_id) and c.classification in(''ordinary_continuing'',''rookie_initial_continuing'',''rookie_initial_taxi_paused'') order by c.contract_agreement_id loop
   select * into a from public.contract_agreements where id=continuing_case.contract_agreement_id for update;
   select * into src from public.contract_seasons where contract_id=a.id and season=(select source_season from public.rollover_executions where id=p_rollover_execution_id) for update;
   select * into tgt from public.contract_seasons where contract_id=a.id and season=(select target_season from public.rollover_executions where id=p_rollover_execution_id) for update;
   if a.status<>''active'' or src.obligation_status<>''active'' or tgt.obligation_status<>''scheduled'' or tgt.is_option_year then perform public.raise_phase3b6c1_failure(''continuing_contract_authority_conflict'',''{}'');end if;
   event_fp:=public.rollover_material_fingerprint(jsonb_build_object(''schema'',''phase3b7b-continuing-advance-v1'',''execution'',p_rollover_execution_id,''agreement'',a.id,''source'',src.id,''target'',tgt.id));
   update public.contract_seasons set obligation_status=''active'',rollover_execution_id=p_rollover_execution_id,rollover_operation_code=code,rollover_final_outcome_id=null,rollover_evidence_hash=event_fp,updated_at=clock_timestamp() where id=tgt.id;
   insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key) values(a.id,a.league_id,a.league_team_id,a.player_id,''season_obligation_advanced'',tgt.season,''phase3b7b_abs_continuing'',p_actor,jsonb_build_object(''obligation_status'',''scheduled''),jsonb_build_object(''obligation_status'',''active''),jsonb_build_object(''rollover_execution_id'',p_rollover_execution_id,''operation_code'',code,''source_contract_season_id'',src.id,''target_contract_season_id'',tgt.id,''event_fingerprint'',event_fp),format(''phase3b7b:%s:%s:continuing:%s'',p_rollover_execution_id,code,a.id));
   continuing:=continuing+1;mutations:=mutations+1;events_written:=events_written+1;
  end loop;
 end if;
 result_hash:=public.rollover_material_fingerprint(jsonb_build_object(''schema'',''phase3b7b-result-v1''';
 d:=replace(d,old_fragment,new_fragment);
 if d not like '%rookie_option_already_consumed_or_provenance_missing%' or d not like '%phase3b7b-continuing-advance-v1%' then raise exception 'ABS_OPTION_CONSUMPTION_HANDLER_PATCH_FAILED';end if;
 execute d;
end$$;

-- Taxi eligibility consumes and grants authority from board provenance, never
-- from generic NFL rookie metadata.
do $$declare d text;old_fragment text;new_fragment text;begin
 select pg_get_functiondef('public.write_taxi_eligibility_authority_phase3b8c_private(uuid,uuid,uuid,uuid)'::regprocedure) into d;
 old_fragment:='if player.rookie_class_year is null or player.draft_year is null or player.draft_round is null then perform public.raise_phase3b6c1_failure(''taxi_eligibility_rookie_evidence_missing'',''{}'');end if;';
 new_fragment:='if not exists(select 1 from public.rookie_draft_board_assignments b where b.league_id=x.league_id and b.player_id=r.player_id and b.rookie_contract_provenance) then perform public.raise_phase3b6c1_failure(''taxi_eligibility_rookie_evidence_missing'',''{}'');end if;';
 d:=replace(d,old_fragment,new_fragment);
 old_fragment:='elsif assignment.id is not null and player.rookie_class_year=x.target_season and player.draft_year=x.target_season and player.draft_round between 1 and 20 then';
 new_fragment:='elsif assignment.id is not null and exists(select 1 from public.rookie_draft_board_assignments b where b.league_id=x.league_id and b.player_id=r.player_id and b.draft_year=x.target_season and b.rookie_contract_provenance) then';
 d:=replace(d,old_fragment,new_fragment);
 if (length(d)-length(replace(d,'rookie_draft_board_assignments b','')))/length('rookie_draft_board_assignments b')<2 then raise exception 'ABS_TAXI_ELIGIBILITY_BOARD_PATCH_FAILED';end if;
 execute d;
end$$;

alter table public.rollover_taxi_unlock_dispositions alter column source_assignment_id drop not null;
do $$declare d text;old_fragment text;new_fragment text;begin
 select pg_get_functiondef('public.write_taxi_unlock_set_phase3b8b_private(uuid,uuid,uuid,uuid)'::regprocedure) into d;
 old_fragment:='for r in select * from public.season_roster_assignments where league_season_id=source_id and roster_designation=''taxi'' order by sleeper_player_id,id loop';
 new_fragment:='for r in select q.id,q.sleeper_player_id from(select sr.id,sr.sleeper_player_id from public.season_roster_assignments sr where sr.league_season_id=source_id and sr.roster_designation=''taxi'' union all select ta.source_roster_assignment_id,ta.player_id from public.rookie_taxi_assignments ta where ta.league_id=x.league_id and ta.league_season_id=source_id and not exists(select 1 from public.season_roster_assignments sr where sr.league_season_id=source_id and sr.sleeper_player_id=ta.player_id))q order by q.sleeper_player_id,q.id nulls last loop';
 d:=replace(d,old_fragment,new_fragment);
 if (length(d)-length(replace(d,'rookie_taxi_assignments ta','')))/length('rookie_taxi_assignments ta')<>2 then raise exception 'ABS_TAXI_UNLOCK_PROVENANCE_PATCH_FAILED';end if;
 execute d;
end$$;

-- Two commissioner-proven taxi rookies were omitted from the July roster
-- capture. Target-roster preparation may use their immutable taxi authority as
-- the source proof; it does not insert or alter a 2025 roster assignment.
alter table public.season_roster_assignments drop constraint season_roster_target_metadata_complete;
alter table public.season_roster_assignments add constraint season_roster_target_metadata_complete check(
 (assignment_set_id is null and contract_agreement_id is null and target_contract_season_id is null and source_assignment_id is null and roster_status is null and provenance is null and deterministic_row_hash is null)
 or (assignment_set_id is not null and contract_agreement_id is not null and target_contract_season_id is not null
  and (source_assignment_id is not null or nullif(provenance->>'taxi_authority_id','') is not null)
  and roster_status='pending_unpublished' and jsonb_typeof(provenance)='object' and deterministic_row_hash~'^[0-9a-f]{64}$'));
do $$declare d text;old_fragment text;new_fragment text;begin
 select pg_get_functiondef('public.validate_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid)'::regprocedure) into d;
 old_fragment:='and not (src.id is null and public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id))) then';
 new_fragment:='and not (src.id is null and (public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id) or exists(select 1 from public.rookie_taxi_assignments ta where ta.league_id=x.league_id and ta.league_season_id=source_id and ta.player_id=a.player_id and ta.league_team_id=a.league_team_id)))) then';
 d:=replace(d,old_fragment,new_fragment);
 if d not like '%rookie_taxi_assignments ta%' then raise exception 'ABS_TARGET_ROSTER_VALIDATOR_TAXI_PATCH_FAILED';end if;
 execute d;
end$$;
do $$declare d text;old_fragment text;new_fragment text;begin
 select pg_get_functiondef('public.write_target_roster_assignment_set_phase3b8a_private(uuid,uuid,uuid,uuid)'::regprocedure) into d;
 old_fragment:='if r.source_assignment_id is null then perform public.raise_phase3b6c1_failure(''target_roster_mapping_incomplete'',''{}'');end if;';
 new_fragment:='if r.source_assignment_id is null and not exists(select 1 from public.rookie_taxi_assignments ta where ta.league_id=x.league_id and ta.league_season_id=source_id and ta.player_id=r.player_id and ta.league_team_id=r.league_team_id) then perform public.raise_phase3b6c1_failure(''target_roster_mapping_incomplete'',''{}'');end if;';
 d:=replace(d,old_fragment,new_fragment);
 old_fragment:='and cs.league_season_id=target_season.id and cs.obligation_status=''active'' join public.player_universe pu on pu.sleeper_id=a.player_id
   join public.season_roster_assignments src on src.league_season_id=source_id and src.sleeper_player_id=a.player_id';
 new_fragment:='and cs.league_season_id=target_season.id and cs.obligation_status=''active'' join public.player_universe pu on pu.sleeper_id=a.player_id
   left join public.season_roster_assignments src on src.league_season_id=source_id and src.sleeper_player_id=a.player_id';
 d:=replace(d,old_fragment,new_fragment);
 old_fragment:='''authorization_authority'',''league_memberships.league_team_id'',''sleeper_authoritative'',false)';
 new_fragment:='''authorization_authority'',''league_memberships.league_team_id'',''taxi_authority_id'',(select ta.id from public.rookie_taxi_assignments ta where ta.league_id=x.league_id and ta.league_season_id=source_id and ta.player_id=r.player_id),''sleeper_authoritative'',false)';
 d:=replace(d,old_fragment,new_fragment);
 if d not like '%taxi_authority_id%' or d not like '%rookie_taxi_assignments ta%' then raise exception 'ABS_TARGET_ROSTER_TAXI_PROVENANCE_PATCH_FAILED';end if;
 execute d;
end$$;

-- Operation 13 gains one narrow candidate source: certified ordinary expirations.
-- It creates the same unpublished controlled release evidence without requiring
-- a fabricated owner-option outcome. Off-roster cases intentionally retain a
-- NULL captured assignment rather than being placed back on a roster.
alter table public.rollover_contract_releases alter column source_roster_assignment_id drop not null;
alter table public.rollover_contract_releases alter column final_outcome_id drop not null;
alter table public.rollover_contract_releases alter column final_outcome_hash drop not null;
do $$declare d text;old_fragment text;new_fragment text;begin
 select pg_get_functiondef('public.execute_rollover_typed_handler_phase3b7c_private(jsonb,uuid,uuid,uuid,uuid)'::regprocedure) into d;
 old_fragment:=' r public.rollover_contract_releases%rowtype;assignment public.season_roster_assignments%rowtype;hold_id uuid;fp text;result_hash text;';
 new_fragment:=' r public.rollover_contract_releases%rowtype;assignment public.season_roster_assignments%rowtype;ordinary_class record;hold_id uuid;fp text;result_hash text;';
 d:=replace(d,old_fragment,new_fragment);
 old_fragment:=' if code=''RELEASE_EXPIRED_CONTRACTS'' then
  for a in select * from public.contract_agreements where rollover_execution_id=p_rollover_execution_id';
 new_fragment:=' if code=''RELEASE_EXPIRED_CONTRACTS'' then
  for ordinary_class in select c.*,a.status agreement_status,a.league_team_id from public.contract_rollover_classifications c join public.contract_agreements a on a.id=c.contract_agreement_id where c.league_id=x.league_id and c.source_season=x.source_season and c.target_season=x.target_season and c.classification=''ordinary_expiration'' order by c.player_id,c.contract_agreement_id loop
   candidates:=candidates+1;select * into assignment from public.season_roster_assignments where league_season_id=(select id from public.league_seasons where league_id=x.league_id and season=x.source_season) and sleeper_player_id=ordinary_class.player_id;
   if assignment.id is not null and assignment.league_team_id<>ordinary_class.league_team_id then perform public.raise_phase3b6c1_failure(''ordinary_release_owner_mismatch'',''{}'');end if;
   fp:=public.rollover_material_fingerprint(jsonb_build_object(''schema'',''phase3b7c-ordinary-expiration-v1'',''execution'',p_rollover_execution_id,''classification'',ordinary_class.id,''agreement'',ordinary_class.contract_agreement_id,''assignment'',assignment.id));
   insert into public.rollover_contract_releases(rollover_execution_id,league_id,closing_season_id,target_season_id,contract_agreement_id,player_id,source_league_team_id,source_roster_assignment_id,final_outcome_id,final_outcome_hash,release_disposition,effective_season,previous_agreement_status,resulting_agreement_status,release_fingerprint)
   values(p_rollover_execution_id,x.league_id,(select id from public.league_seasons where league_id=x.league_id and season=x.source_season),(select id from public.league_seasons where league_id=x.league_id and season=x.target_season),ordinary_class.contract_agreement_id,ordinary_class.player_id,ordinary_class.league_team_id,assignment.id,null,null,''ordinary_release'',x.target_season,ordinary_class.agreement_status,''released'',fp) returning * into r;
   update public.contract_agreements set status=''released'',updated_at=clock_timestamp() where id=ordinary_class.contract_agreement_id and status=''expired'';
   if not found then perform public.raise_phase3b6c1_failure(''ordinary_release_state_conflict'',''{}'');end if;
   insert into public.contract_events(contract_id,league_id,league_team_id,player_id,event_type,effective_season,source,actor_user_id,previous_values,new_values,metadata,idempotency_key)
   values(ordinary_class.contract_agreement_id,x.league_id,ordinary_class.league_team_id,ordinary_class.player_id,''released'',x.target_season,''phase3b7c_ordinary_expiration'',p_actor,jsonb_build_object(''status'',ordinary_class.agreement_status),jsonb_build_object(''status'',''released'',''ownership'',''closed'',''public_visibility'',false),jsonb_build_object(''rollover_execution_id'',p_rollover_execution_id,''operation_code'',code,''release_id'',r.id,''classification_id'',ordinary_class.id,''event_fingerprint'',fp),format(''phase3b7c:%s:%s:ordinary:%s'',p_rollover_execution_id,code,ordinary_class.contract_agreement_id));
   ordinary:=ordinary+1;agreements:=agreements+1;ownership_closed:=ownership_closed+1;evidence:=evidence+1;
  end loop;
  for a in select * from public.contract_agreements where rollover_execution_id=p_rollover_execution_id';
 d:=replace(d,old_fragment,new_fragment);
 old_fragment:='if candidates<>(select count(*) from public.contract_agreements where rollover_execution_id=p_rollover_execution_id and rollover_pending_disposition is not null) then';
 new_fragment:='if candidates<>(select count(*) from public.contract_agreements where rollover_execution_id=p_rollover_execution_id and rollover_pending_disposition is not null)+(select count(*) from public.contract_rollover_classifications where league_id=x.league_id and source_season=x.source_season and target_season=x.target_season and classification=''ordinary_expiration'') then';
 d:=replace(d,old_fragment,new_fragment);
 if d not like '%phase3b7c-ordinary-expiration-v1%' or d not like '%ordinary_release_state_conflict%' or d like '%'||old_fragment||'%' then raise exception 'ABS_ORDINARY_RELEASE_HANDLER_PATCH_FAILED';end if;
 execute d;
end$$;

-- Forward-only authenticated board persistence. The RPC requires a canonical
-- team and player; historical NULL ownership cannot be reproduced by this path.
create or replace function public.persist_rookie_draft_board_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;yr int:=(p_request->>'draft_year')::int;p jsonb;written int:=0;begin perform public.require_commissioner_authority(lid);if jsonb_typeof(p_request->'picks')<>'array' then raise exception 'rookie board picks required';end if;for p in select value from jsonb_array_elements(p_request->'picks') loop if not exists(select 1 from public.league_teams where id=(p->>'league_team_id')::uuid and league_id=lid) or not exists(select 1 from public.player_universe where sleeper_id=p->>'player_id') then raise exception 'rookie board canonical identity invalid';end if;insert into public.rookie_draft_board_assignments(league_id,player_id,original_league_team_id,draft_year,draft_round,round_pick,overall_pick,rookie_contract_provenance,original_salary,original_contract_term,one_time_option_salary,one_time_option_term,option_consumed,source_type,source_event,deterministic_fingerprint) values(lid,p->>'player_id',(p->>'league_team_id')::uuid,yr,(p->>'draft_round')::int,(p->>'round_pick')::int,((p->>'draft_round')::int-1)*10+(p->>'round_pick')::int,true,(p->>'original_salary')::numeric,(p->>'original_contract_term')::int,(p->>'one_time_option_salary')::numeric,1,false,'rookie_draft_board_assignment',jsonb_build_object('actor',actor,'request_id',p_request->>'idempotency_key'),public.rollover_material_fingerprint(jsonb_build_object('league',lid,'year',yr,'round',p->>'draft_round','pick',p->>'round_pick','player',p->>'player_id','team',p->>'league_team_id','salary',p->>'original_salary','term',p->>'original_contract_term','option_salary',p->>'one_time_option_salary','option_term',1))) on conflict(league_id,draft_year,overall_pick) do nothing;written:=written+1;end loop;return jsonb_build_object('rows_written',written,'draft_year',yr);end$$;
revoke all on function public.persist_rookie_draft_board_authenticated(jsonb) from public,anon,authenticated,service_role;grant execute on function public.persist_rookie_draft_board_authenticated(jsonb) to authenticated;

create or replace function public.persist_rookie_taxi_assignment_authenticated(p_request jsonb) returns jsonb language plpgsql security definer set search_path=pg_catalog,public as $$declare actor uuid:=public.require_authenticated_user();lid uuid:=(p_request->>'league_id')::uuid;pid text:=p_request->>'player_id';tid uuid:=(p_request->>'league_team_id')::uuid;sid uuid:=(p_request->>'league_season_id')::uuid;board public.rookie_draft_board_assignments%rowtype;assignment public.season_roster_assignments%rowtype;normal numeric:=(p_request->>'normal_annual_charge')::numeric;row_id uuid;begin perform public.require_commissioner_authority(lid);select * into board from public.rookie_draft_board_assignments where league_id=lid and player_id=pid order by draft_year,id limit 1;if board.id is null then raise exception 'taxi requires canonical Rookie Draft Board provenance';end if;select * into assignment from public.season_roster_assignments where league_season_id=sid and sleeper_player_id=pid;if assignment.id is null or assignment.league_team_id<>tid or not exists(select 1 from public.league_seasons where id=sid and league_id=lid) then raise exception 'taxi canonical ownership missing';end if;insert into public.rookie_taxi_assignments(league_id,player_id,league_team_id,league_season_id,rookie_draft_assignment_id,source_roster_assignment_id,normal_annual_charge,taxi_charge,contract_year_consumed,locked,unlock_target_season,evidence,deterministic_fingerprint) values(lid,pid,tid,sid,board.id,assignment.id,normal,round(normal*.5,2),false,true,(select season+1 from public.league_seasons where id=sid),jsonb_build_object('actor',actor,'assigned_before_season',true,'source','authenticated_taxi_assignment'),public.rollover_material_fingerprint(jsonb_build_object('league',lid,'player',pid,'team',tid,'season',sid,'board',board.id,'normal',normal,'taxi',round(normal*.5,2),'consumed',false,'locked',true))) returning id into row_id;return jsonb_build_object('taxi_assignment_id',row_id,'contract_year_consumed',false,'locked',true,'taxi_charge',round(normal*.5,2));end$$;
revoke all on function public.persist_rookie_taxi_assignment_authenticated(jsonb) from public,anon,authenticated,service_role;grant execute on function public.persist_rookie_taxi_assignment_authenticated(jsonb) to authenticated;

-- Certify exact postconditions while preserving all historical evidence.
with material as(select jsonb_build_object('agreements',(select jsonb_agg(to_jsonb(a)-'updated_at' order by a.id) from public.contract_agreements a where a.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe'),'seasons',(select jsonb_agg(to_jsonb(s)-'updated_at' order by s.id) from public.contract_seasons s where s.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe'),'classifications',(select jsonb_agg(to_jsonb(c)-'created_at' order by c.contract_agreement_id) from public.contract_rollover_classifications c where c.league_id='9838a0a1-97c6-4cab-bb88-af177317abfe')) value)
update public.contract_transition_reconciliations r set reconciliation_status='certified',after_fingerprint=public.rollover_material_fingerprint(material.value),actual_counts=jsonb_build_object('source_active',211,'target_active',0,'target_scheduled',98,'agreements_active',95,'agreements_expired',116,'season_2027_scheduled',38,'prepared_options',3,'taxi_paused',9,'ordinary_expirations',113),certified_at=clock_timestamp() from material where r.reconciliation_key='abs:2025:2026:legacy-transition-reconciliation:v1';

do $$declare ready jsonb;begin ready:=public.rollover_contract_preflight_readiness_private('9838a0a1-97c6-4cab-bb88-af177317abfe',2025,2026);if ready->>'ready'<>'true' or jsonb_array_length(ready->'blockers')<>0 or (ready->>'agreement_count')::int<>211 or (ready->>'source_count')::int<>211 or (ready->>'prepared_option_count')::int<>3 or (ready->>'active_target_count')::int<>0 or (ready->>'prior_transition_count')::int<>0 or (ready->>'unresolved_provenance_count')::int<>0 then raise exception 'ABS_RECONCILIATION_POSTCONDITION_FAILED:%',ready;end if;
if (select count(*) from public.contract_events where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and event_type='expired' and metadata->>'transition_key'='contract-transition:9838a0a1-97c6-4cab-bb88-af177317abfe:2025:2026:v1')<>119 or not exists(select 1 from public.contract_transition_executions where id='cbcef849-5ba2-4b1a-88bc-b285f73b0740' and status='validated') or exists(select 1 from public.rollover_executions where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and source_season=2025 and target_season=2026) or not exists(select 1 from public.league_seasons where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and season=2025 and status='active' and is_active) or not exists(select 1 from public.league_seasons where league_id='9838a0a1-97c6-4cab-bb88-af177317abfe' and season=2026 and status='scheduled' and not is_active) then raise exception 'ABS_RECONCILIATION_SAFETY_POSTCONDITION_FAILED';end if;end$$;

commit;
