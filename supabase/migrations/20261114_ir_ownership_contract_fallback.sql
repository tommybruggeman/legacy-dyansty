-- Accept canonical IR ownership from contract authority as well as roster
-- assignments.
--
-- The original IR authority required a season_roster_assignments row. That
-- table is produced by the rollover engine, so a league that has not yet
-- executed a rollover has none, and every owner IR assignment failed with
-- 'IR canonical roster ownership mismatch'.
--
-- The Taxi authority already handles this: it prefers the roster assignment
-- and falls back to draft-board provenance. This applies the same shape to IR,
-- falling back to contract_seasons -- the canonical contract authority the
-- function already consults on the next statement to resolve the charge.
--
-- Ownership is still enforced. When a roster assignment exists it must match
-- the team exactly; only its absence defers to contract authority.
-- Route IR designation through canonical owner-or-commissioner authority.
begin;
set local search_path = pg_catalog, public;

create or replace function public.persist_injured_reserve_assignment_authenticated(
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
    season_year integer;
    membership_count integer;
    actor_role text;
    actor_team_id uuid;
    canonical_owner text;
    canonical_player_name text;
    normal numeric;
    adjustment numeric;
    row_id uuid;
    assignment public.season_roster_assignments%rowtype;
begin
    if lid is null or pid is null or tid is null or sid is null
       or requested_normal is null or requested_normal < 0 then
        raise exception 'invalid canonical IR assignment request';
    end if;

    select ls.season
      into season_year
      from public.league_seasons ls
     where ls.id = sid
       and ls.league_id = lid
       and ls.is_active;
    if season_year is null then
        raise exception 'IR active league season authority missing';
    end if;

    select lt.owner_name
      into canonical_owner
      from public.league_teams lt
     where lt.id = tid
       and lt.league_id = lid;
    if canonical_owner is null then
        raise exception 'IR canonical team authority missing';
    end if;

    select count(*)
      into membership_count
      from public.league_memberships m
     where m.league_id = lid
       and m.user_id = actor;
    if membership_count <> 1 then
        raise exception 'IR league membership authority missing or ambiguous';
    end if;
    select lower(m.role), m.league_team_id
      into actor_role, actor_team_id
      from public.league_memberships m
     where m.league_id = lid
       and m.user_id = actor;
    if actor_role in ('commissioner', 'admin', 'host') then
        null;
    elsif actor_team_id = tid then
        null;
    else
        raise exception 'IR team authority required';
    end if;

    -- Canonical ownership may be proven by either source, mirroring how the
    -- Taxi authority already degrades. season_roster_assignments is produced by
    -- the rollover engine, so a league that has not yet rolled over has no rows
    -- there while its contract authority is complete and correct.
    select *
      into assignment
      from public.season_roster_assignments sra
     where sra.league_season_id = sid
       and sra.sleeper_player_id = pid
     order by sra.id
     limit 1;

    if assignment.id is not null then
        if assignment.league_team_id <> tid then
            raise exception 'IR canonical roster ownership mismatch';
        end if;
    elsif not exists (
        select 1
          from public.contract_seasons cs
         where cs.league_season_id = sid
           and cs.league_team_id = tid
           and cs.player_id = pid
           and cs.obligation_status in ('active', 'scheduled')
    ) then
        raise exception 'IR canonical roster ownership mismatch';
    end if;

    if exists (
        select 1
          from public.cap_adjustments ca
         where ca.league_id = lid
           and ca.season = season_year
           and ca.adjustment_type = 'ir_adjustment'
           and lower(btrim(ca.owner_name)) = lower(btrim(canonical_owner))
    ) then
        raise exception 'IR slot is already occupied for this team and season';
    end if;

    select cs.cap_hit
      into normal
      from public.contract_seasons cs
     where cs.league_season_id = sid
       and cs.league_team_id = tid
       and cs.player_id = pid
       and cs.obligation_status in ('active', 'scheduled')
     order by case cs.obligation_status when 'active' then 0 else 1 end, cs.id
     limit 1;
    if normal is null or requested_normal is distinct from normal then
        raise exception 'IR normal annual charge does not match canonical authority';
    end if;

    select coalesce(nullif(btrim(pu.player_name), ''), pid)
      into canonical_player_name
      from public.player_universe pu
     where pu.sleeper_id = pid
     order by pu.updated_at desc nulls last, pu.sleeper_id
     limit 1;
    canonical_player_name := coalesce(canonical_player_name, pid);
    adjustment := round(-(normal / 2), 2);

    insert into public.cap_adjustments (
        league_id, owner_name, player_name, sleeper_player_id, season,
        adjustment_type, amount, note
    ) values (
        lid, canonical_owner, canonical_player_name, pid, season_year,
        'ir_adjustment', adjustment, 'Injured Reserve designation'
    ) returning id into row_id;

    return jsonb_build_object(
        'ir_adjustment_id', row_id,
        'amount', adjustment,
        'authorization', case when actor_role in ('commissioner', 'admin', 'host')
            then 'commissioner_override' else 'owner_self_service' end
    );
end
$$;

revoke all on function
    public.persist_injured_reserve_assignment_authenticated(jsonb)
from public, anon, authenticated, service_role;

grant execute on function
    public.persist_injured_reserve_assignment_authenticated(jsonb)
to authenticated;

commit;
