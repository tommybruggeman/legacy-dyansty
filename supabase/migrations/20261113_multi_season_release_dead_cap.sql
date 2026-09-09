-- Multi-season dead cap for Sleeper-sourced drops.
--
-- release_offseason_player_authenticated writes a single obligation for the
-- active season and voids every future contract season. The league rule is
-- that the drop penalty follows the contract for each year still owed, so
-- this appends the remaining seasons alongside that release rather than
-- reimplementing it. Additive only: the existing release path is untouched.
begin;
set local search_path = pg_catalog, public;

create or replace function public.append_release_dead_cap_schedule_authenticated(
    p_request jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    actor uuid := public.require_authenticated_user();
    lid uuid := (p_request->>'league_id')::uuid;
    tid uuid := (p_request->>'league_team_id')::uuid;
    pid text := nullif(btrim(p_request->>'player_id'), '');
    ikey text := nullif(btrim(p_request->>'release_idempotency_key'), '');
    release_event public.contract_events%rowtype;
    entry jsonb;
    entry_season integer;
    entry_amount numeric;
    dead_event_id uuid;
    written integer := 0;
begin
    perform public.require_commissioner_authority(lid);
    perform public.assert_no_active_rollover_cutover_lock(lid);

    if lid is null or tid is null or pid is null or ikey is null then
        raise exception 'invalid dead cap schedule request';
    end if;

    select * into release_event
      from public.contract_events
     where idempotency_key = ikey
       and event_type = 'released'
       and league_id = lid
       and league_team_id = tid
       and player_id = pid;
    if release_event.id is null then
        raise exception 'release event for this schedule was not found';
    end if;

    for entry in select * from jsonb_array_elements(coalesce(p_request->'schedule', '[]'::jsonb))
    loop
        entry_season := (entry->>'season')::int;
        entry_amount := round((entry->>'amount')::numeric, 2);

        -- The release itself already charged the active season.
        if entry_season is null or entry_season <= release_event.effective_season then
            continue;
        end if;
        if entry_amount is null or entry_amount <= 0 then
            continue;
        end if;

        insert into public.contract_events(
            contract_id, league_id, league_team_id, player_id, event_type,
            effective_season, source, actor_user_id, new_values, metadata, idempotency_key
        ) values (
            release_event.contract_id, lid, tid, pid, 'dead_cap_created',
            entry_season, 'commissioner_manual_drop', actor,
            jsonb_build_object('dead_cap_amount', entry_amount),
            jsonb_build_object(
                'dead_cap_amount', entry_amount,
                'penalty_rule', 'sleeper_multi_season_release',
                'release_event_id', release_event.id
            ),
            ikey || ':dead-cap-event:' || entry_season
        )
        on conflict (idempotency_key) do nothing
        returning id into dead_event_id;

        if dead_event_id is null then
            continue;
        end if;

        insert into public.dead_cap_obligations(
            league_id, league_team_id, player_id, contract_agreement_id, season,
            amount, source_event_id, termination_type, calculation_rule, status,
            created_by, metadata, idempotency_key
        ) values (
            lid, tid, pid, release_event.contract_id, entry_season,
            entry_amount, dead_event_id, 'commissioner_adjustment',
            'sleeper_multi_season_release', 'active', actor,
            jsonb_build_object('release_event_id', release_event.id),
            ikey || ':dead-cap:' || entry_season
        )
        on conflict (idempotency_key) do nothing;

        written := written + 1;
        dead_event_id := null;
    end loop;

    return jsonb_build_object(
        'contract_agreement_id', release_event.contract_id,
        'player_id', pid,
        'seasons_written', written
    );
end
$$;

revoke all on function
    public.append_release_dead_cap_schedule_authenticated(jsonb)
from public, anon, authenticated, service_role;

grant execute on function
    public.append_release_dead_cap_schedule_authenticated(jsonb)
to authenticated;

commit;
