\set ON_ERROR_STOP on
do $$begin create role anon;exception when duplicate_object then null;end$$;
do $$begin create role authenticated;exception when duplicate_object then null;end$$;
do $$begin create role service_role;exception when duplicate_object then null;end$$;
create schema auth;
create table public.league_memberships(id uuid,league_id uuid,user_id uuid,role text,league_team_id uuid);
create table public.league_teams(id uuid,league_id uuid,owner_name text,team_name text);
create table public.player_universe(sleeper_id text,player_name text,pos text);
create table public.contract_agreements(id uuid,league_id uuid,league_team_id uuid,player_id text,sleeper_player_id text,contract_type text,status text,superseded_by_contract_id uuid);
create table public.contract_seasons(contract_id uuid,league_id uuid,league_team_id uuid,player_id text,season int,salary numeric,cap_hit numeric,obligation_status text);
create table public.dead_cap_obligations(id uuid,league_id uuid,league_team_id uuid,player_id text,season int,amount numeric,status text);
create table public.contract_events(id uuid,contract_id uuid,league_id uuid,league_team_id uuid,player_id text,event_type text,effective_season int,effective_at timestamptz,created_at timestamptz,idempotency_key text);
create table public.cap_adjustments(id uuid,league_id uuid,owner_name text,player_name text,sleeper_player_id text,season int,adjustment_type text,amount numeric,created_at timestamptz);
create function public.require_authenticated_user() returns uuid language plpgsql stable security definer set search_path=pg_catalog,public as $$declare u uuid:=nullif(current_setting('test.actor',true),'')::uuid;begin if u is null then raise exception 'Authentication required';end if;return u;end$$;
insert into league_teams values
 ('10000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','Chase','Chase Seyforth'),
 ('10000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','Other','Other Team'),
 ('20000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','Cross','Cross Team');
insert into league_memberships values
 ('30000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000001','member','10000000-0000-0000-0000-000000000001'),
 ('30000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','40000000-0000-0000-0000-000000000002','commissioner',null);
insert into player_universe values('11628','Marvin Harrison Jr.','WR'),('p-add','Added Player','RB');
insert into contract_agreements values('50000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','p-add','p-add','veteran','active',null);
insert into contract_seasons values('50000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','p-add',2026,7,7,'active'),('50000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','p-add',2027,7,7,'scheduled');
insert into dead_cap_obligations values('60000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','11628',2026,20.50,'active');
insert into contract_events values('70000000-0000-0000-0000-000000000001','50000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','11628','released',2026,now(),now(),'release:1');
insert into contract_events values('70000000-0000-0000-0000-000000000002','50000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','p-add','signed',2026,now()-interval '1 day',now()-interval '1 day','sign:1');
insert into cap_adjustments values('80000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','Chase','IR Player','ir',2026,'ir_adjustment',2,now());
\ir ../migrations/20261030_authenticated_canonical_team_state_read.sql

do $$declare r jsonb;begin
 perform set_config('test.actor','40000000-0000-0000-0000-000000000001',true);
 r:=public.read_canonical_team_state_authenticated('{"league_id":"00000000-0000-0000-0000-000000000001","season":2026}'::jsonb);
 if jsonb_array_length(r->'teams')<>1 or jsonb_array_length(r->'roster')<>1 or jsonb_array_length(r->'dead_cap')<>1 or jsonb_array_length(r->'activity')<>2 then raise exception 'owner result cardinality failed: %',r;end if;
 if r#>>'{dead_cap,0,player_name}'<>'Marvin Harrison Jr.' or r#>>'{activity,0,action}'<>'drop' or r#>>'{roster,0,contract_years_left}'<>'2' then raise exception 'canonical enrichment failed: %',r;end if;
 if r#>>'{activity,1,action}'<>'add' then raise exception 'signed event normalization failed: %',r;end if;
 begin perform public.read_canonical_team_state_authenticated('{"league_id":"00000000-0000-0000-0000-000000000001","season":2026,"league_team_id":"10000000-0000-0000-0000-000000000002"}'::jsonb);raise exception 'cross-team read unexpectedly passed';exception when others then if sqlerrm='cross-team read unexpectedly passed'then raise;end if;end;
 perform set_config('test.actor','40000000-0000-0000-0000-000000000002',true);
 r:=public.read_canonical_team_state_authenticated('{"league_id":"00000000-0000-0000-0000-000000000001","season":2026}'::jsonb);
 if jsonb_array_length(r->'teams')<>2 then raise exception 'commissioner league scope failed';end if;
 begin perform public.read_canonical_team_state_authenticated('{"league_id":"00000000-0000-0000-0000-000000000002","season":2026}'::jsonb);raise exception 'cross-league read unexpectedly passed';exception when others then if sqlerrm='cross-league read unexpectedly passed'then raise;end if;end;
 perform set_config('test.actor','',true);
 begin perform public.read_canonical_team_state_authenticated('{"league_id":"00000000-0000-0000-0000-000000000001","season":2026}'::jsonb);raise exception 'anonymous read unexpectedly passed';exception when others then if sqlerrm='anonymous read unexpectedly passed'then raise;end if;end;
end$$;
select '20261030 authenticated canonical team state: PASS' as result;
