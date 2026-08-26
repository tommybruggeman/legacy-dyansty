begin;

alter table public.league_rules
 add column if not exists scale_rookie_salaries_with_cap boolean not null default false,
 add column if not exists rookie_salary_scale_base_cap numeric not null default 225;

do $$begin
 if not exists(select 1 from pg_constraint where conname='league_rules_rookie_salary_scale_base_cap_positive') then
  alter table public.league_rules add constraint league_rules_rookie_salary_scale_base_cap_positive
   check(rookie_salary_scale_base_cap>0);
 end if;
end$$;

create or replace function public.resolve_offseason_contract_terms_private(
 p_league_id uuid,p_acquisition_type text,p_actual_amount numeric default null,p_years integer default null)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare rules public.league_rules%rowtype;salary numeric;term integer;minimum_bid numeric;
begin
 select * into strict rules from public.league_rules where league_id=p_league_id;
 if p_acquisition_type='ordinary_fa' then salary:=rules.league_min_salary;term:=1;
 elsif p_acquisition_type='waiver' then salary:=greatest(coalesce(p_actual_amount,0),rules.league_min_salary);term:=1;
 elsif p_acquisition_type='fa_auction' then
  salary:=p_actual_amount;term:=p_years;
  if salary is null or term is null or term<1 or term>rules.max_contract_years then raise exception 'auction contract terms are invalid';end if;
  minimum_bid:=case term when 2 then rules.min_2_year_bid when 3 then rules.min_3_year_bid when 4 then rules.min_4_year_bid else rules.league_min_salary end;
  if salary<minimum_bid then raise exception 'auction salary is below the term minimum bid';end if;
  if rules.year_discount_pct not between 0 and 100 then raise exception 'auction year discount percentage is invalid';end if;
 else raise exception 'unsupported canonical acquisition type';end if;
 return jsonb_build_object('salary',salary,'years',term,'minimum_salary',rules.league_min_salary,
  'year_discount_pct',rules.year_discount_pct);
exception when no_data_found then raise exception 'canonical league rules are missing';
end$$;

create or replace function public.calculate_default_drop_dead_cap_private(p_league_id uuid,p_salary numeric)
returns numeric language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare percentage numeric;
begin
 select default_dead_cap_pct into strict percentage from public.league_rules where league_id=p_league_id;
 if percentage not between 0 and 100 or p_salary<0 then raise exception 'default dead cap rule is invalid';end if;
 return round(p_salary*percentage/100,2);
exception when no_data_found then raise exception 'canonical league rules are missing';
end$$;

create or replace function public.resolve_rookie_contract_terms_private(
 p_league_id uuid,p_draft_round integer,p_round_pick integer)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,public as $$
declare rules public.league_rules%rowtype;base_salary numeric;option_salary numeric;term integer;multiplier numeric:=1;
begin
 select * into strict rules from public.league_rules where league_id=p_league_id;
 if not coalesce(rules.rookie_scale_enabled,true) then raise exception 'canonical rookie salary scale is disabled';end if;
 if p_round_pick not between 1 and 10 or p_draft_round not between 1 and 3 then raise exception 'canonical rookie salary scale entry is missing';end if;
 base_salary:=case p_draft_round when 1 then (array[15,12,9,8,6,5,4,4,4,4]::numeric[])[p_round_pick] when 2 then 3 when 3 then 1 end;
 option_salary:=case p_draft_round when 1 then 25 when 2 then 15 when 3 then 7 end;
 term:=case when p_draft_round in(1,2) then 2 else 1 end;
 if rules.scale_rookie_salaries_with_cap then
  if rules.salary_cap<=0 or rules.rookie_salary_scale_base_cap<=0 then raise exception 'rookie salary scale cap authority is invalid';end if;
  multiplier:=rules.salary_cap/rules.rookie_salary_scale_base_cap;
 end if;
 return jsonb_build_object('base_salary',base_salary,'salary',round(base_salary*multiplier,0),'years',term,
  'base_option_salary',option_salary,'option_salary',round(option_salary*multiplier,0),'option_years',1,
  'salary_cap',rules.salary_cap,'base_cap',rules.rookie_salary_scale_base_cap,'scaled',rules.scale_rookie_salaries_with_cap);
exception when no_data_found then raise exception 'canonical league rules are missing';
end$$;

-- Resolve the canonical terms before any replay comparison or write. Supplied
-- rookie salary/term values are intentionally replaced, never trusted.
do $$declare d text;needle text;replacement text;begin
 select pg_get_functiondef('public.persist_rookie_draft_board_authenticated(jsonb)'::regprocedure) into d;
 if d like '%resolve_rookie_contract_terms_private%' then return;end if;
 needle:=E' for p in select value from jsonb_array_elements(p_request->''picks'') loop\n';
 replacement:=needle||E'  p:=p||jsonb_build_object(''original_salary'',public.resolve_rookie_contract_terms_private(lid,(p->>''draft_round'')::int,(p->>''round_pick'')::int)->>''salary'',''original_contract_term'',public.resolve_rookie_contract_terms_private(lid,(p->>''draft_round'')::int,(p->>''round_pick'')::int)->>''years'',''one_time_option_salary'',public.resolve_rookie_contract_terms_private(lid,(p->>''draft_round'')::int,(p->>''round_pick'')::int)->>''option_salary'');\n';
 if d not like '%'||needle||'%' then raise exception 'rookie contract resolver patch point missing';end if;
 execute replace(d,needle,replacement);
end$$;

do $$declare d text;needle text;replacement text;begin
 select pg_get_functiondef('public.acquire_offseason_player_private(jsonb,uuid)'::regprocedure) into d;
 if d like '%resolve_offseason_contract_terms_private%' then return;end if;
 needle:='if active_season is null or (kind<>''rookie_draft'' and yr<>active_season) or (kind=''rookie_draft'' and yr not in(active_season,active_season+1)) then raise exception ''acquisition season is not authorized by canonical season authority'';end if;';
 replacement:=needle||' if kind=''fa_auction'' then perform public.resolve_offseason_contract_terms_private(lid,''fa_auction'',amount,term);end if;';
 if d not like '%'||needle||'%' then raise exception 'auction rule resolver patch point missing';end if;
 execute replace(d,needle,replacement);
end$$;

revoke all on function public.resolve_rookie_contract_terms_private(uuid,integer,integer),
 public.resolve_offseason_contract_terms_private(uuid,text,numeric,integer),
 public.calculate_default_drop_dead_cap_private(uuid,numeric) from public,anon,authenticated,service_role;

commit;
