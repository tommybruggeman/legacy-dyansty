\set ON_ERROR_STOP on
\ir 20261030_authenticated_canonical_team_state_read_test.sql

create table public.league_seasons(id uuid,league_id uuid,season int);
create table public.rookie_taxi_assignments(id uuid,league_id uuid,player_id text,league_team_id uuid,league_season_id uuid,locked boolean,unlocked_at timestamptz);
insert into league_seasons values('90000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001',2026);
insert into player_universe values('taxi-player','Taxi Rookie','RB'),('ir-player','IR Player','WR'),('both-player','Both Player','TE');
insert into contract_agreements values
 ('50000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','taxi-player','taxi-player','rookie','active',null),
 ('50000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','ir-player','ir-player','veteran','active',null),
 ('50000000-0000-0000-0000-000000000004','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','both-player','both-player','rookie','active',null);
insert into contract_seasons values
 ('50000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','taxi-player',2026,4,4,'active'),
 ('50000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','ir-player',2026,8,8,'active'),
 ('50000000-0000-0000-0000-000000000004','00000000-0000-0000-0000-000000000001','10000000-0000-0000-0000-000000000001','both-player',2026,5,5,'active');
insert into rookie_taxi_assignments values
 ('91000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000001','taxi-player','10000000-0000-0000-0000-000000000001','90000000-0000-0000-0000-000000000001',true,null),
 ('91000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','both-player','10000000-0000-0000-0000-000000000001','90000000-0000-0000-0000-000000000001',true,null);
insert into cap_adjustments values
 ('80000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','Chase','Wrong Name Is Ignored','ir-player',2026,'ir_adjustment',0,now()),
 ('80000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000001','Chase','Both Player',null,2026,'ir_adjustment',0,now());
\ir ../migrations/20261101_canonical_team_state_roster_designation.sql

do $$declare r jsonb;begin
 perform set_config('test.actor','40000000-0000-0000-0000-000000000001',true);
 r:=public.read_canonical_team_state_authenticated('{"league_id":"00000000-0000-0000-0000-000000000001","season":2026}'::jsonb);
 if (select value->>'roster_designation' from jsonb_array_elements(r->'roster') where value->>'sleeper_player_id'='taxi-player')<>'taxi' then raise exception 'taxi designation failed: %',r;end if;
 if (select (value->>'is_rookie')::boolean from jsonb_array_elements(r->'roster') where value->>'sleeper_player_id'='taxi-player') is not true then raise exception 'rookie provenance failed: %',r;end if;
 if (select value->>'roster_designation' from jsonb_array_elements(r->'roster') where value->>'sleeper_player_id'='ir-player')<>'ir' then raise exception 'stable-id IR designation failed: %',r;end if;
 if (select value->>'roster_designation' from jsonb_array_elements(r->'roster') where value->>'sleeper_player_id'='both-player')<>'taxi' then raise exception 'taxi precedence failed: %',r;end if;
 if (select value->'roster_designation' from jsonb_array_elements(r->'roster') where value->>'sleeper_player_id'='p-add')<>'null'::jsonb then raise exception 'ordinary designation failed: %',r;end if;
end$$;
select '20261101 canonical roster designation: PASS' result;
