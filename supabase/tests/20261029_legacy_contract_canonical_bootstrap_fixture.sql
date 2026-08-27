\set ON_ERROR_STOP on
begin;
insert into auth.users(id,email) values
 ('00000000-0000-0000-0000-000000000001','commissioner@example.test'),
 ('00000000-0000-0000-0000-000000000002','member@example.test'),
 ('00000000-0000-0000-0000-000000000003','foreign@example.test');

insert into public.leagues(id,name,created_by) values
 ('b03edc51-bec1-4064-9201-72e48ba413f9','Disposable Bootstrap League','00000000-0000-0000-0000-000000000001'),
 ('00000000-0000-0000-0000-000000000099','Foreign League','00000000-0000-0000-0000-000000000003');

insert into public.league_teams(id,league_id,owner_name,team_name,sleeper_roster_id)
select ('10000000-0000-0000-0000-'||lpad(g::text,12,'0'))::uuid,
 'b03edc51-bec1-4064-9201-72e48ba413f9','Owner '||g,'Team '||g,g from generate_series(1,10) g;
insert into public.league_teams(id,league_id,owner_name,team_name) values
 ('10000000-0000-0000-0000-000000000099','00000000-0000-0000-0000-000000000099','Foreign Owner','Foreign Team');

insert into public.league_memberships(league_id,user_id,role,league_team_id) values
 ('b03edc51-bec1-4064-9201-72e48ba413f9','00000000-0000-0000-0000-000000000001','commissioner','10000000-0000-0000-0000-000000000001'),
 ('b03edc51-bec1-4064-9201-72e48ba413f9','00000000-0000-0000-0000-000000000002','member','10000000-0000-0000-0000-000000000002'),
 ('00000000-0000-0000-0000-000000000099','00000000-0000-0000-0000-000000000003','commissioner','10000000-0000-0000-0000-000000000099');

insert into public.league_seasons(id,league_id,season,is_active,status) values
 ('20000000-0000-0000-0000-000000002026','b03edc51-bec1-4064-9201-72e48ba413f9',2026,true,null),
 ('20000000-0000-0000-0000-000000002027','b03edc51-bec1-4064-9201-72e48ba413f9',2027,false,'scheduled'),
 ('20000000-0000-0000-0000-000000002028','b03edc51-bec1-4064-9201-72e48ba413f9',2028,false,'scheduled'),
 ('20000000-0000-0000-0000-000000002029','b03edc51-bec1-4064-9201-72e48ba413f9',2029,false,'scheduled'),
 ('20000000-0000-0000-0000-000000002030','b03edc51-bec1-4064-9201-72e48ba413f9',2030,false,'scheduled'),
 ('20000000-0000-0000-0000-000000009926','00000000-0000-0000-0000-000000000099',2026,true,'active');
update public.league_seasons set created_at=clock_timestamp()+interval '1 minute'
where league_id='b03edc51-bec1-4064-9201-72e48ba413f9' and is_active;

insert into public.league_rules(league_id,salary_cap,league_min_salary,default_dead_cap_pct,
 max_contract_years,min_2_year_bid,min_3_year_bid,min_4_year_bid,year_discount_pct,
 rookie_scale_enabled,scale_rookie_salaries_with_cap,rookie_salary_scale_base_cap)
values('b03edc51-bec1-4064-9201-72e48ba413f9',225,1,50,4,4,12,20,10,true,false,225),
 ('00000000-0000-0000-0000-000000000099',225,1,50,4,4,12,20,10,true,false,225);

insert into public.player_universe(sleeper_id,canonical_player_id,player_name,search_name,pos,nfl_status,active,
 rookie_class_year,draft_year,draft_round,draft_pick,market_pool,nfl_team)
select 'player-'||g,'canonical-'||g,case when g=1 then 'Marvin Fixture' else 'Player '||g end,
 'player '||g,case g%4 when 0 then 'QB' when 1 then 'RB' when 2 then 'WR' else 'TE' end,
 'ACTIVE',true,case when g in(95,96,97) then 2026 end,case when g in(95,96,97) then 2026 end,
 case when g in(95,96,97) then 1 end,case when g in(95,96,97) then g-94 end,
 case when g in(95,96,97) then 'ROOKIE_PROSPECT' else 'VETERAN' end,'TST'
from generate_series(1,100) g;

insert into public.contracts(league_id,sleeper_player_id,player_name,player_position,owner_name,
 contract_total_years,contract_years_left,salary,is_rookie)
select 'b03edc51-bec1-4064-9201-72e48ba413f9','player-'||g,
 case when g=1 then 'Marvin Fixture' else 'Player '||g end,
 case g%4 when 0 then 'QB' when 1 then 'RB' when 2 then 'WR' else 'TE' end,
 'Owner '||((g-1)%10+1),(g%3)+2,(g%3)+1,(g%25)+1,g%5=0
from generate_series(1,92) g;
commit;
