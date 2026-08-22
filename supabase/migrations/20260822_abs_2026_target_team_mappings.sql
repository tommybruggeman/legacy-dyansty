begin;

-- ============================================================
-- ABs 2025 -> 2026 target team-mapping preparation
--
-- Preconditions:
--   * exact production league
--   * 2026 target season exists and is scheduled
--   * exactly 10 canonical teams
--   * canonical Sleeper roster IDs are exactly 1..10
--   * no target mappings already exist
--
-- This prepares target-season identity only.
-- It does NOT execute rollover operations or publish 2026.
-- ============================================================

do $$
declare
    v_league_id constant uuid :=
        '9838a0a1-97c6-4cab-bb88-af177317abfe'::uuid;

    v_target_season_id uuid;
    v_team_count integer;
    v_target_count integer;
begin
    select id
    into v_target_season_id
    from public.league_seasons
    where league_id = v_league_id
      and season = 2026
      and status = 'scheduled'
      and not is_active;

    if v_target_season_id is null then
        raise exception
            'Expected exactly one scheduled inactive 2026 target season.';
    end if;

    select count(*)
    into v_team_count
    from public.league_teams
    where league_id = v_league_id;

    if v_team_count <> 10 then
        raise exception
            'Expected 10 canonical league teams; found %.',
            v_team_count;
    end if;

    if exists (
        select 1
        from public.league_teams
        where league_id = v_league_id
          and sleeper_roster_id is null
    ) then
        raise exception
            'Canonical team without Sleeper roster ID.';
    end if;

    if (
        select count(distinct sleeper_roster_id)
        from public.league_teams
        where league_id = v_league_id
    ) <> 10 then
        raise exception
            'Canonical Sleeper roster IDs are not unique.';
    end if;

    if exists (
        select 1
        from generate_series(1,10) expected(roster_id)
        left join public.league_teams t
          on t.league_id = v_league_id
         and t.sleeper_roster_id = expected.roster_id
        where t.id is null
    ) then
        raise exception
            'Expected canonical Sleeper roster IDs 1 through 10.';
    end if;

    select count(*)
    into v_target_count
    from public.season_team_mappings
    where league_season_id = v_target_season_id;

    if v_target_count <> 0 then
        raise exception
            'Target season already has % team mappings; refusing to modify.',
            v_target_count;
    end if;

    insert into public.season_team_mappings (
        league_season_id,
        league_team_id,
        sleeper_roster_id,
        sleeper_owner_id,
        sleeper_user_id,
        team_name_snapshot,
        owner_name_snapshot,
        mapping_source,
        mapping_confidence
    )
    select
        v_target_season_id,
        t.id,
        t.sleeper_roster_id,

        case t.sleeper_roster_id
            when 1  then '1136471569439813632'
            when 2  then '617957679079444480'
            when 3  then '741370077013807104'
            when 4  then '840365995624349696'
            when 5  then '617909946259947520'
            when 6  then '1136474182021521408'
            when 7  then '864936593700540416'
            when 8  then '1136478372818903040'
            when 9  then '1136483167034404864'
            when 10 then '1136730773895348224'
        end,

        case t.sleeper_roster_id
            when 1  then '1136471569439813632'
            when 2  then '617957679079444480'
            when 3  then '741370077013807104'
            when 4  then '840365995624349696'
            when 5  then '617909946259947520'
            when 6  then '1136474182021521408'
            when 7  then '864936593700540416'
            when 8  then '1136478372818903040'
            when 9  then '1136483167034404864'
            when 10 then '1136730773895348224'
        end,

        t.team_name,
        t.owner_name,
        'verified_2026_sleeper_roster_authority',
        'exact'

    from public.league_teams t
    where t.league_id = v_league_id
    order by t.sleeper_roster_id;

    select count(*)
    into v_target_count
    from public.season_team_mappings
    where league_season_id = v_target_season_id;

    if v_target_count <> 10 then
        raise exception
            'Postcondition failed: expected 10 target mappings; found %.',
            v_target_count;
    end if;
end
$$;

commit;
