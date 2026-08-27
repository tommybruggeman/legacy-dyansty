-- 20261029_canonical_rookie_taxi_assignment.sql
--
-- Canonical rookie Taxi assignment authority.
--
-- Incoming rookies may establish canonical ownership through either:
--   1. season_roster_assignments, or
--   2. current-season rookie_draft_board_assignments provenance.
--
-- Taxi economics remain authoritative:
--   - 50% of normal annual charge
--   - no contract year consumed
--   - locked until rollover
--   - canonical row persisted in rookie_taxi_assignments

create or replace function public.persist_rookie_taxi_assignment_authenticated(
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
    pid text := p_request->>'player_id';
    tid uuid := (p_request->>'league_team_id')::uuid;
    sid uuid := (p_request->>'league_season_id')::uuid;
    normal numeric := (p_request->>'normal_annual_charge')::numeric;

    season_year integer;

    board public.rookie_draft_board_assignments%rowtype;
    assignment public.season_roster_assignments%rowtype;

    source_assignment_id uuid;
    row_id uuid;
begin
    perform public.require_commissioner_authority(lid);

    if lid is null
       or nullif(pid, '') is null
       or tid is null
       or sid is null
       or normal is null
       or normal < 0 then
        raise exception 'invalid canonical taxi assignment request';
    end if;

    select ls.season
      into season_year
      from public.league_seasons ls
     where ls.id = sid
       and ls.league_id = lid;

    if season_year is null then
        raise exception 'taxi league season authority missing';
    end if;

    /*
     * Current-season rookie draft provenance is the canonical Taxi
     * eligibility authority.
     */
    select *
      into board
      from public.rookie_draft_board_assignments b
     where b.league_id = lid
       and b.player_id = pid
       and b.draft_year = season_year
       and b.original_league_team_id = tid
       and b.rookie_contract_provenance
     order by b.id
     limit 1;

    if board.id is null then
        raise exception 'taxi requires current-season canonical Rookie Draft Board provenance';
    end if;

    /*
     * A season roster assignment is valid ownership evidence when present,
     * but incoming drafted rookies are also canonically owned through the
     * draft-board assignment itself.
     */
    select *
      into assignment
      from public.season_roster_assignments sra
     where sra.league_season_id = sid
       and sra.sleeper_player_id = pid
     order by sra.id
     limit 1;

    if assignment.id is not null then
        if assignment.league_team_id <> tid then
            raise exception 'taxi canonical ownership mismatch';
        end if;

        source_assignment_id := assignment.id;
    else
        source_assignment_id := null;
    end if;

    insert into public.rookie_taxi_assignments (
        league_id,
        player_id,
        league_team_id,
        league_season_id,
        rookie_draft_assignment_id,
        source_roster_assignment_id,
        normal_annual_charge,
        taxi_charge,
        contract_year_consumed,
        locked,
        unlock_target_season,
        evidence,
        deterministic_fingerprint
    )
    values (
        lid,
        pid,
        tid,
        sid,
        board.id,
        source_assignment_id,
        normal,
        round(normal * 0.50, 2),
        false,
        true,
        season_year + 1,
        jsonb_build_object(
            'actor', actor,
            'assigned_before_season', true,
            'source', 'authenticated_taxi_assignment',
            'ownership_authority',
                case
                    when source_assignment_id is null
                        then 'rookie_draft_board_assignments'
                    else 'season_roster_assignments'
                end
        ),
        public.rollover_material_fingerprint(
            jsonb_build_object(
                'league', lid,
                'player', pid,
                'team', tid,
                'season', sid,
                'board', board.id,
                'source_roster_assignment', source_assignment_id,
                'normal', normal,
                'taxi', round(normal * 0.50, 2),
                'consumed', false,
                'locked', true
            )
        )
    )
    returning id into row_id;

    return jsonb_build_object(
        'taxi_assignment_id', row_id,
        'contract_year_consumed', false,
        'locked', true,
        'taxi_charge', round(normal * 0.50, 2),
        'ownership_authority',
            case
                when source_assignment_id is null
                    then 'rookie_draft_board_assignments'
                else 'season_roster_assignments'
            end
    );
end
$$;

revoke all on function
    public.persist_rookie_taxi_assignment_authenticated(jsonb)
from public, anon, authenticated, service_role;

grant execute on function
    public.persist_rookie_taxi_assignment_authenticated(jsonb)
to authenticated;
