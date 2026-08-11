begin;

-- Prepared team caps include preserved contract liabilities independently of
-- roster membership. The same immutable snapshot proof used by Operation 15
-- is the sole authority for this non-roster cap charge.
do $$
declare
 definition text;
 original_fragment text := 'select coalesce(sum(c.salary),0),count(*) into active,ac from public.season_roster_assignments r join public.contract_seasons c on c.id=r.target_contract_season_id where r.assignment_set_id=aset.id and r.league_team_id=t.id and c.obligation_status=''active'';';
 replacement_fragment text := 'select coalesce(sum(z.amount),0),count(*) into active,ac from (select c.salary amount from public.season_roster_assignments r join public.contract_seasons c on c.id=r.target_contract_season_id where r.assignment_set_id=aset.id and r.league_team_id=t.id and c.obligation_status=''active'' union all select c.cap_hit amount from public.contract_agreements a join public.contract_seasons c on c.contract_id=a.id and c.league_season_id=aset.target_league_season_id where a.league_id=x.league_id and a.league_team_id=t.id and a.status=''active'' and public.phase3b8a_is_preserved_off_roster_liability(s.id,a.id,a.player_id,a.league_team_id)) z;';
begin
 select pg_get_functiondef(
  'public.write_prepared_caps_phase3b10b_private(uuid,uuid,uuid,uuid)'::regprocedure
 ) into definition;
 if position(original_fragment in definition)=0 then
  if position(replacement_fragment in definition)>0 then return;end if;
  raise exception 'phase3b10b deployed cap query shape is not recognized';
 end if;
 execute replace(definition,original_fragment,replacement_fragment);
end$$;

commit;
