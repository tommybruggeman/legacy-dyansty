-- Rookie deals across every rookie draft a league has had (Tommy, 2026-11-20).
-- The 2025 rookie draft lives under the 2025 league row; the 2026 draft (and every later one) under the live
-- league. league_lineage links a league to the one before it, so a member of the live league can read the whole
-- history without being a member of the old league. Read only: nothing here changes a contract.
begin;

create table if not exists public.league_lineage (
  league_id uuid primary key references public.leagues(id),
  previous_league_id uuid not null references public.leagues(id),
  created_at timestamptz not null default now(),
  check (league_id <> previous_league_id)
);
alter table public.league_lineage enable row level security;
revoke all on public.league_lineage from anon, authenticated;

-- Live 2026 league follows the 2025 league. Add one row like this when a new league row starts a season.
insert into public.league_lineage (league_id, previous_league_id)
values ('b03edc51-bec1-4064-9201-72e48ba413f9', '9838a0a1-97c6-4cab-bb88-af177317abfe')
on conflict (league_id) do nothing;

-- One row per drafted player (latest draft if drafted twice):
--   expected_end  = draft year + rookie term - 1 + one extra season for every season he spent on taxi
--   rookie_deal   = his current contract in this league is still that rookie deal: same first-year salary,
--                   ending on the rookie end year (or the year before it, when the taxi year hasn't been added
--                   to the contract yet). A re-signed, released or option-year contract is not a rookie deal.
create or replace function public.read_rookie_deals_authenticated(p_league_id uuid)
returns table (
  player_id text, draft_year integer, draft_round integer, overall_pick integer,
  original_salary numeric, original_term integer, option_salary numeric, option_consumed boolean,
  taxi_seasons integer, expected_end integer, contract_end integer, rookie_deal boolean
)
language sql stable security definer set search_path = pg_catalog, public as $$
  with recursive chain(lid, depth) as (
    select p_league_id, 0
    union
    select l.previous_league_id, c.depth + 1 from public.league_lineage l join chain c on l.league_id = c.lid where c.depth < 20
  ),
  member as (
    select 1 from public.league_memberships m where m.league_id = p_league_id and m.user_id = auth.uid()
  ),
  board as (
    select distinct on (b.player_id) b.*
    from public.rookie_draft_board_assignments b
    where b.league_id in (select lid from chain) and b.rookie_contract_provenance
    order by b.player_id, b.draft_year desc
  ),
  taxi as (
    select t.player_id, count(distinct ls.season)::integer as n
    from public.rookie_taxi_assignments t join public.league_seasons ls on ls.id = t.league_season_id
    where t.league_id in (select lid from chain)
    group by t.player_id
  ),
  cur as (
    select distinct on (a.sleeper_player_id) a.sleeper_player_id as pid, a.end_season,
      (select s.salary from public.contract_seasons s where s.contract_id = a.id order by s.season limit 1) as first_salary
    from public.contract_agreements a
    where a.league_id = p_league_id and a.status in ('active', 'scheduled') and a.superseded_by_contract_id is null
    order by a.sleeper_player_id, a.start_season desc
  )
  select b.player_id, b.draft_year, b.draft_round, b.overall_pick,
         b.original_salary, b.original_contract_term, b.one_time_option_salary, b.option_consumed,
         coalesce(t.n, 0),
         b.draft_year + b.original_contract_term - 1 + coalesce(t.n, 0),
         c.end_season,
         (c.pid is not null and c.first_salary = b.original_salary
          and c.end_season between b.draft_year + b.original_contract_term - 1 and b.draft_year + b.original_contract_term - 1 + coalesce(t.n, 0))
  from board b
  left join taxi t on t.player_id = b.player_id
  left join cur c on c.pid = b.player_id
  where exists (select 1 from member);
$$;

revoke all on function public.read_rookie_deals_authenticated(uuid) from public, anon;
grant execute on function public.read_rookie_deals_authenticated(uuid) to authenticated;

commit;
