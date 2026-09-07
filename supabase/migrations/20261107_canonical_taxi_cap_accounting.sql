-- Derive designation-dependent Taxi liability in the canonical team-state
-- read model. Contract salary and contract_seasons.cap_hit remain unchanged.
begin;
set local search_path = pg_catalog, public;

alter function public.read_canonical_team_state_authenticated_pre_trade(jsonb)
    rename to read_canonical_team_state_authenticated_pre_taxi_cap;

revoke all on function
    public.read_canonical_team_state_authenticated_pre_taxi_cap(jsonb)
from public, anon, authenticated, service_role;

create function public.read_canonical_team_state_authenticated_pre_trade(
    p_request jsonb
)
returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, public
as $$
declare
    base jsonb;
    lid uuid;
    yr integer;
    effective_roster jsonb;
begin
    base := public.read_canonical_team_state_authenticated_pre_taxi_cap(p_request);
    lid := (base->>'league_id')::uuid;
    yr := (base->>'season')::integer;

    select coalesce(jsonb_agg(
        roster_row
        || jsonb_build_object(
            'contract_cap_hit', (roster_row->>'cap_hit')::numeric,
            'normal_annual_charge', coalesce(
                taxi.normal_annual_charge,
                (roster_row->>'cap_hit')::numeric
            ),
            'cap_hit', coalesce(
                taxi.taxi_charge,
                (roster_row->>'cap_hit')::numeric
            )
        )
        || case when taxi.id is null then '{}'::jsonb else jsonb_build_object(
            'taxi_assignment_id', taxi.id::text,
            'taxi_charge', taxi.taxi_charge,
            'taxi_locked', taxi.locked,
            'taxi_contract_year_consumed', taxi.contract_year_consumed
        ) end
        order by roster_row->>'owner_name', roster_row->>'pos', roster_row->>'player_name'
    ), '[]'::jsonb)
      into effective_roster
      from jsonb_array_elements(base->'roster') roster_row
      left join lateral (
          select ta.*
            from public.rookie_taxi_assignments ta
            join public.league_seasons ls
              on ls.id = ta.league_season_id
             and ls.league_id = ta.league_id
           where ta.league_id = lid
             and ls.season = yr
             and ta.league_team_id = (roster_row->>'league_team_id')::uuid
             and ta.player_id = roster_row->>'sleeper_player_id'
             and ta.locked
             and ta.unlocked_at is null
           limit 1
      ) taxi on true;

    return jsonb_set(base, '{roster}', effective_roster, false);
end
$$;

revoke all on function
    public.read_canonical_team_state_authenticated_pre_trade(jsonb)
from public, anon, authenticated, service_role;

-- This remains an internal hop used only by the outer authenticated reader.
-- Its underlying function performs the auth.uid()/membership enforcement.
revoke all on function
    public.read_canonical_team_state_authenticated_pre_taxi_cap(jsonb)
from public, anon, authenticated, service_role;

commit;
