-- Allow authenticated owners to fill their own canonical Taxi slot while
-- preserving commissioner intervention and the existing rollover authority.
begin;
set local search_path = pg_catalog, public;

-- The slot is a team/season resource, not merely a player/season resource.
create unique index if not exists rookie_taxi_one_slot_per_team_season
    on public.rookie_taxi_assignments (league_season_id, league_team_id);

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
    pid text := nullif(btrim(p_request->>'player_id'), '');
    tid uuid := (p_request->>'league_team_id')::uuid;
    sid uuid := (p_request->>'league_season_id')::uuid;
    requested_normal numeric := (p_request->>'normal_annual_charge')::numeric;
    normal numeric;
    season_year integer;
    membership_count integer;
    actor_role text;
    actor_team_id uuid;
    board public.rookie_draft_board_assignments%rowtype;
    assignment public.season_roster_assignments%rowtype;
    source_assignment_id uuid;
    row_id uuid;
begin
    if lid is null or pid is null or tid is null or sid is null
       or requested_normal is null or requested_normal < 0 then
        raise exception 'invalid canonical taxi assignment request';
    end if;

    select ls.season
      into season_year
      from public.league_seasons ls
     where ls.id = sid
       and ls.league_id = lid
       and ls.is_active;

    if season_year is null then
        raise exception 'taxi active league season authority missing';
    end if;

    if not exists (
        select 1 from public.league_teams t
         where t.id = tid and t.league_id = lid
    ) then
        raise exception 'taxi canonical team authority missing';
    end if;

    select count(*)
      into membership_count
      from public.league_memberships m
     where m.league_id = lid
       and m.user_id = actor;

    if membership_count <> 1 then
        raise exception 'taxi league membership authority missing or ambiguous';
    end if;

    select lower(m.role), m.league_team_id
      into actor_role, actor_team_id
      from public.league_memberships m
     where m.league_id = lid
       and m.user_id = actor;

    if actor_role in ('commissioner', 'admin', 'host') then
        null; -- Existing commissioner intervention remains supported.
    elsif actor_team_id = tid then
        null; -- Owner self-service is restricted to the canonical linked team.
    else
        raise exception 'taxi team authority required';
    end if;

    -- Serialize all contenders for the one team/season slot.
    perform pg_advisory_xact_lock(
        hashtextextended('rookie-taxi-slot:' || sid::text || ':' || tid::text, 0)
    );

    if exists (
        select 1
          from public.rookie_taxi_assignments ta
         where ta.league_season_id = sid
           and ta.league_team_id = tid
    ) then
        raise exception 'taxi slot is locked for this team and season';
    end if;

    select *
      into board
      from public.rookie_draft_board_assignments b
     where b.league_id = lid
       and b.player_id = pid
       and b.draft_year = season_year
       and b.rookie_contract_provenance
     order by b.id
     limit 1;

    if board.id is null then
        raise exception 'taxi requires current-season canonical Rookie Draft Board provenance';
    end if;

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
    elsif board.original_league_team_id <> tid then
        raise exception 'taxi canonical ownership mismatch';
    end if;
    source_assignment_id := assignment.id;

    -- Resolve the normal charge from canonical contract authority. A freshly
    -- drafted rookie may not yet have a contract-season row, in which case the
    -- immutable draft-board salary is authoritative.
    select cs.cap_hit
      into normal
      from public.contract_seasons cs
     where cs.league_season_id = sid
       and cs.league_team_id = tid
       and cs.player_id = pid
       and cs.obligation_status in ('active', 'scheduled')
     order by case cs.obligation_status when 'active' then 0 else 1 end, cs.id
     limit 1;
    normal := coalesce(normal, board.original_salary);

    if normal is null or normal < 0
       or requested_normal is distinct from normal then
        raise exception 'taxi normal annual charge does not match canonical authority';
    end if;

    insert into public.rookie_taxi_assignments (
        league_id, player_id, league_team_id, league_season_id,
        rookie_draft_assignment_id, source_roster_assignment_id,
        normal_annual_charge, taxi_charge, contract_year_consumed, locked,
        unlock_target_season, evidence, deterministic_fingerprint
    ) values (
        lid, pid, tid, sid, board.id, source_assignment_id,
        normal, round(normal * 0.50, 2), false, true, season_year + 1,
        jsonb_build_object(
            'actor', actor,
            'actor_role', actor_role,
            'assigned_before_season', true,
            'source', 'authenticated_taxi_assignment',
            'authorization_authority', 'league_memberships.league_team_id',
            'ownership_authority', case when source_assignment_id is null
                then 'rookie_draft_board_assignments'
                else 'season_roster_assignments' end
        ),
        public.rollover_material_fingerprint(jsonb_build_object(
            'league', lid, 'player', pid, 'team', tid, 'season', sid,
            'board', board.id, 'source_roster_assignment', source_assignment_id,
            'normal', normal, 'taxi', round(normal * 0.50, 2),
            'consumed', false, 'locked', true
        ))
    ) returning id into row_id;

    return jsonb_build_object(
        'taxi_assignment_id', row_id,
        'contract_year_consumed', false,
        'locked', true,
        'taxi_charge', round(normal * 0.50, 2),
        'authorization', case when actor_role in ('commissioner', 'admin', 'host')
            then 'commissioner_override' else 'owner_self_service' end,
        'ownership_authority', case when source_assignment_id is null
            then 'rookie_draft_board_assignments'
            else 'season_roster_assignments' end
    );
end
$$;

revoke all on function
    public.persist_rookie_taxi_assignment_authenticated(jsonb)
from public, anon, authenticated, service_role;

grant execute on function
    public.persist_rookie_taxi_assignment_authenticated(jsonb)
to authenticated;

commit;
