-- A released agreement is financial history, not player ownership.
--
-- Some pre-canonical drop paths preserved the dead-cap/release evidence but
-- did not close the agreement row.  Both the database acquisition guard and
-- roster consumers must therefore require a live season obligation and must
-- reject release evidence, rather than trusting agreement.status alone.
begin;
set local search_path = pg_catalog, public;

-- Tre' Harris was imported from a stale legacy row even though Sleeper roster
-- 2 had released him in November 2025.  This is an import correction, not a
-- player release: void the agreement and both obligations without dead cap.
-- The exact identity checks make the repair fail closed if production drifted.
do $$
declare
    agreement public.contract_agreements%rowtype;
    season_count integer;
begin
    select * into agreement
      from public.contract_agreements
     where id = 'cf117b5a-929d-480e-83d8-4b20a348ce67'::uuid
       and league_id = 'b03edc51-bec1-4064-9201-72e48ba413f9'::uuid
       and player_id = '12509'
       and source_legacy_contract_id = '30141a1e-edb1-415a-869f-32341771d1ca'::uuid
       and origin = 'imported_initial_contract'
       and status = 'active'
     for update;
    if agreement.id is null then
        raise exception 'Tre Harris bootstrap agreement identity or state changed';
    end if;

    select count(*) into season_count
      from public.contract_seasons
     where contract_id = agreement.id
       and ((season = 2026 and obligation_status = 'active')
         or (season = 2027 and obligation_status = 'scheduled'));
    if season_count <> 2 or exists (
        select 1 from public.contract_seasons
         where contract_id = agreement.id and season not in (2026, 2027)
    ) then
        raise exception 'Tre Harris bootstrap season obligations changed';
    end if;
    if exists (
        select 1 from public.dead_cap_obligations
         where contract_agreement_id = agreement.id
    ) or exists (
        select 1 from public.contract_events
         where contract_id = agreement.id
           and event_type in ('released', 'dead_cap_created', 'voided')
    ) then
        raise exception 'Tre Harris bootstrap correction already has termination evidence';
    end if;

    perform set_config(
        'app.contract_transition_execution',
        'contract-transition-executor-v1',
        true
    );
    update public.contract_seasons
       set obligation_status = 'voided', updated_at = clock_timestamp()
     where contract_id = agreement.id and season in (2026, 2027);
    update public.contract_agreements
       set status = 'voided', updated_at = clock_timestamp()
     where id = agreement.id;
    insert into public.contract_events(
        contract_id, league_id, league_team_id, player_id, event_type,
        effective_season, source, previous_values, new_values, metadata,
        idempotency_key
    ) values (
        agreement.id, agreement.league_id, agreement.league_team_id,
        agreement.player_id, 'voided', 2026, 'legacy_import_correction',
        jsonb_build_object('agreement_status', 'active',
                           '2026_obligation_status', 'active',
                           '2027_obligation_status', 'scheduled'),
        jsonb_build_object('agreement_status', 'voided',
                           '2026_obligation_status', 'voided',
                           '2027_obligation_status', 'voided'),
        jsonb_build_object('reason', 'stale legacy contract imported after prior Sleeper drop',
                           'dead_cap', 0,
                           'sleeper_drop_transaction_id', '1296578059884843008'),
        'repair:tre-harris-stale-bootstrap:void'
    );
end
$$;

create or replace function public.player_has_live_contract_private(
    p_league_id uuid,
    p_player_id text,
    p_active_season integer
)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public
as $$
    select exists (
        select 1
          from public.contract_agreements a
         where a.league_id = p_league_id
           and a.player_id = p_player_id
           and a.status in ('active', 'scheduled')
           and a.superseded_by_contract_id is null
           and exists (
               select 1
                 from public.contract_seasons s
                where s.contract_id = a.id
                  and s.season >= p_active_season
                  and s.obligation_status in ('active', 'scheduled')
           )
           and not exists (
               select 1
                 from public.contract_events e
                where e.contract_id = a.id
                  and e.event_type in ('released', 'dead_cap_created')
           )
    )
$$;

-- 20261115 copied the acquisition function and accidentally restored its old
-- status-only ownership test.  Patch that exact guard while retaining the
-- Sleeper acquisition kinds added there.
do $$
declare
    definition text;
    old_guard text := 'if exists(select 1 from public.contract_agreements where league_id=lid and player_id=pid and status in(''active'',''scheduled'') and superseded_by_contract_id is null) then raise exception ''player already has canonical active ownership'';end if;';
    new_guard text := 'select season into active_season from public.league_seasons where league_id=lid and is_active and status=''active'' for share;if active_season is null then raise exception ''acquisition season is not authorized by canonical season authority'';end if;if public.player_has_live_contract_private(lid,pid,active_season) then raise exception ''player already has canonical active ownership'';end if;';
begin
    if to_regprocedure(
        'public.acquire_offseason_player_private(jsonb,uuid)'
    ) is null then
        return;
    end if;
    select pg_get_functiondef(
        'public.acquire_offseason_player_private(jsonb,uuid)'::regprocedure
    ) into definition;
    if definition like '%player_has_live_contract_private(lid,pid,active_season)%' then
        return;
    end if;
    if definition not like '%' || old_guard || '%' then
        raise exception 'release-aware acquisition guard patch point missing';
    end if;
    execute replace(definition, old_guard, new_guard);
end
$$;

revoke all on function
    public.player_has_live_contract_private(uuid, text, integer)
from public, anon, authenticated, service_role;

commit;
