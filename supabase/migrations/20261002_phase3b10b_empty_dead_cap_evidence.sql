begin;

-- Existing deployments must canonicalize an empty dead-cap evidence set as []
-- rather than passing SQL NULL to the material fingerprint function.
do $$
declare
 definition text;
 original_fragment text := 'public.rollover_material_fingerprint((select jsonb_agg(deterministic_fingerprint order by contract_agreement_id) from public.rollover_dead_cap_obligations where rollover_execution_id=x.id))';
 replacement_fragment text := 'public.rollover_material_fingerprint(coalesce((select jsonb_agg(deterministic_fingerprint order by contract_agreement_id) from public.rollover_dead_cap_obligations where rollover_execution_id=x.id),''[]''::jsonb))';
begin
 select pg_get_functiondef(
  'public.write_prepared_caps_phase3b10b_private(uuid,uuid,uuid,uuid)'::regprocedure
 ) into definition;
 if position(original_fragment in definition)=0 then
  if position(replacement_fragment in definition)>0 then return;end if;
  raise exception 'phase3b10b deployed function shape is not recognized';
 end if;
 execute replace(definition,original_fragment,replacement_fragment);
end$$;

commit;
