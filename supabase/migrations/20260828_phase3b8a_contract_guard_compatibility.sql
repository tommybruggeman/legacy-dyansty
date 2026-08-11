begin;

create or replace function public.guard_contract_write_during_rollover()
returns trigger language plpgsql security definer set search_path=pg_catalog,public as $$
declare lid uuid:=coalesce(new.league_id,old.league_id);
begin
 if coalesce(current_setting('app.rollover_typed_execution',true),'') not in('phase3b7c-v1','phase3b8a-v1') then
  perform public.assert_no_active_rollover_cutover_lock(lid);
 end if;
 return case when tg_op='DELETE' then old else new end;
end$$;

revoke all on function public.guard_contract_write_during_rollover()
 from public,anon,authenticated,service_role;

commit;
