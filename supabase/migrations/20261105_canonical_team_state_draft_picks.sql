-- Expose canonical draft-pick ownership through the authenticated team-state
-- read boundary. This keeps draft_pick_assets protected from direct client
-- reads while allowing league members to browse league-wide pick ownership.

create or replace function public.read_canonical_team_state_authenticated(p_request jsonb)
returns jsonb
language plpgsql
stable
security definer
set search_path=pg_catalog,public
as $$
declare
  base jsonb;
  lid uuid;
  yr int;
  scope_id uuid;
begin
  base:=public.read_canonical_team_state_authenticated_pre_trade(p_request);
  lid:=(base->>'league_id')::uuid;
  yr:=(base->>'season')::int;
  scope_id:=nullif(base->>'scope_team_id','')::uuid;

  return base
    || jsonb_build_object(
      'retained_salary',
      coalesce((
        select jsonb_agg(
          to_jsonb(x)
          order by x.player_name,x.season,x.retaining_league_team_id
        )
        from (
          select
            r.id::text id,
            r.league_id::text league_id,
            r.trade_id::text trade_id,
            r.player_id,
            coalesce(p.player_name,r.player_id,'Player') player_name,
            r.contract_agreement_id::text contract_agreement_id,
            r.contract_season_id::text contract_season_id,
            r.retaining_league_team_id::text retaining_league_team_id,
            r.receiving_league_team_id_at_creation::text receiving_league_team_id_at_creation,
            r.season,
            r.amount,
            r.status,
            r.reason,
            r.source,
            r.created_at
          from public.trade_retained_salary_obligations r
          join public.contract_agreements a
            on a.id=r.contract_agreement_id
           and a.league_id=r.league_id
          left join public.player_universe p
            on p.sleeper_id=r.player_id
          where r.league_id=lid
            and r.season=yr
            and r.status='active'
            and (
              scope_id is null
              or r.retaining_league_team_id=scope_id
              or a.league_team_id=scope_id
            )
        ) x
      ),'[]'::jsonb),

      'draft_picks',
      coalesce((
        select jsonb_agg(
          to_jsonb(x)
          order by x.draft_year,x.round_number,x.original_owner_name
        )
        from (
          select
            d.stable_pick_id::text stable_pick_id,
            d.draft_year,
            d.round_number,
            d.asset_status,
            d.original_league_team_id::text original_league_team_id,
            original_team.owner_name original_owner_name,
            coalesce(nullif(original_team.team_name,''),original_team.owner_name)
              original_team_name,
            d.current_owner_league_team_id::text current_owner_league_team_id,
            current_team.owner_name current_owner_name,
            coalesce(nullif(current_team.team_name,''),current_team.owner_name)
              current_team_name
          from public.draft_pick_assets d
          join public.league_teams original_team
            on original_team.id=d.original_league_team_id
           and original_team.league_id=d.league_id
          join public.league_teams current_team
            on current_team.id=d.current_owner_league_team_id
           and current_team.league_id=d.league_id
          where d.league_id=lid
            and (
              scope_id is null
              or d.current_owner_league_team_id=scope_id
            )
        ) x
      ),'[]'::jsonb)
    );
end
$$;

revoke all on function public.read_canonical_team_state_authenticated(jsonb)
from public,anon,authenticated,service_role;

grant execute on function public.read_canonical_team_state_authenticated(jsonb)
to authenticated;
