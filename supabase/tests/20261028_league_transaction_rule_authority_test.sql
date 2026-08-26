begin;

do $$declare lid uuid;r jsonb;begin
 select league_id into lid from public.league_rules order by created_at,league_id limit 1;
 if lid is null then raise exception 'disposable league rules fixture missing';end if;

 update public.league_rules set salary_cap=225,rookie_salary_scale_base_cap=225,
  rookie_scale_enabled=true,scale_rookie_salaries_with_cap=false,
  league_min_salary=1,max_contract_years=4,min_2_year_bid=4,min_3_year_bid=12,min_4_year_bid=20,
  year_discount_pct=10,default_dead_cap_pct=50 where league_id=lid;
 r:=public.resolve_rookie_contract_terms_private(lid,1,3);
 if (r->>'salary')::numeric<>9 or (r->>'years')::int<>2 then raise exception 'unscaled rookie terms incorrect';end if;

 update public.league_rules set scale_rookie_salaries_with_cap=true,salary_cap=250 where league_id=lid;
 if (public.resolve_rookie_contract_terms_private(lid,1,3)->>'salary')::numeric<>10
  or (public.resolve_rookie_contract_terms_private(lid,1,2)->>'salary')::numeric<>13 then
  raise exception 'increased-cap rookie scaling incorrect';end if;
 update public.league_rules set salary_cap=200 where league_id=lid;
 if (public.resolve_rookie_contract_terms_private(lid,1,3)->>'salary')::numeric<>8 then raise exception 'decreased-cap rookie scaling incorrect';end if;
 update public.league_rules set salary_cap=275 where league_id=lid;
 if (public.resolve_rookie_contract_terms_private(lid,1,3)->>'salary')::numeric<>11 then raise exception 'rookie scaling compounded';end if;

 if (public.resolve_offseason_contract_terms_private(lid,'ordinary_fa')->>'salary')::numeric<>1
  or (public.resolve_offseason_contract_terms_private(lid,'ordinary_fa')->>'years')::int<>1
  or (public.resolve_offseason_contract_terms_private(lid,'waiver',7,null)->>'salary')::numeric<>7
  or (public.resolve_offseason_contract_terms_private(lid,'waiver',0,null)->>'salary')::numeric<>1 then
  raise exception 'ordinary FA or waiver authority incorrect';end if;
 if (public.resolve_offseason_contract_terms_private(lid,'fa_auction',12,3)->>'salary')::numeric<>12 then raise exception 'auction terms changed';end if;
 begin perform public.resolve_offseason_contract_terms_private(lid,'fa_auction',11,3);raise exception 'invalid auction accepted';exception when others then if sqlerrm='invalid auction accepted' then raise;end if;end;
 if public.calculate_default_drop_dead_cap_private(lid,9)<>4.50 then raise exception 'default dead cap calculation incorrect';end if;

 begin perform public.resolve_rookie_contract_terms_private(lid,4,1);raise exception 'missing rookie scale accepted';exception when others then if sqlerrm='missing rookie scale accepted' then raise;end if;end;
 if has_function_privilege('authenticated','public.resolve_rookie_contract_terms_private(uuid,integer,integer)','execute')
  or has_function_privilege('authenticated','public.resolve_offseason_contract_terms_private(uuid,text,numeric,integer)','execute')
  or has_function_privilege('authenticated','public.calculate_default_drop_dead_cap_private(uuid,numeric)','execute') then
  raise exception 'private rule helper privilege boundary invalid';end if;
end$$;

rollback;
