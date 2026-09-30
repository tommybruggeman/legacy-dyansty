-- League AI settings: league-specific rules the co-GM relies on that league_rules lacks.
-- Members read; commissioners write.
begin;

create table if not exists public.league_ai_settings (
  league_id uuid primary key references public.leagues(id) on delete cascade,
  taxi_cap_fraction numeric not null default 0.5 check (taxi_cap_fraction between 0 and 1),
  ir_cap_fraction numeric not null default 0.5 check (ir_cap_fraction between 0 and 1),
  taxi_limit integer not null default 1 check (taxi_limit >= 0),
  ir_limit integer not null default 1 check (ir_limit >= 0),
  roster_max integer not null default 22 check (roster_max > 0),
  taxi_rookies_only boolean not null default true,
  taxi_locked_full_season boolean not null default true,
  taxi_skips_contract_year boolean not null default true,
  max_trade_teams integer not null default 4 check (max_trade_teams between 2 and 12),
  faab_dollar_per_salary numeric not null default 1 check (faab_dollar_per_salary >= 0),
  faab_zero_counts_as numeric not null default 1 check (faab_zero_counts_as >= 0),
  faab_pickup_years integer not null default 1 check (faab_pickup_years >= 1),
  one_dollar_deals_no_dead_cap boolean not null default true,
  traded_faab_moves_cap boolean not null default true,
  win_points integer not null default 2,
  top_scorer_bonus_points integer not null default 1,
  top_scorer_bonus_count integer not null default 5,
  playoff_teams integer not null default 6,
  standings_tiebreaker text not null default 'season points for',
  trade_deadline text not null default '',
  house_rules text not null default '',
  scoring_settings jsonb not null default '{}'::jsonb,
  roster_positions jsonb not null default '[]'::jsonb,
  scoring_source text not null default 'sleeper',
  scoring_synced_at timestamptz,
  updated_at timestamptz not null default now()
);

alter table public.league_ai_settings enable row level security;

drop policy if exists league_ai_settings_member_select on public.league_ai_settings;
create policy league_ai_settings_member_select on public.league_ai_settings
for select to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_settings.league_id and lm.user_id = auth.uid()));

drop policy if exists league_ai_settings_commissioner_insert on public.league_ai_settings;
create policy league_ai_settings_commissioner_insert on public.league_ai_settings
for insert to authenticated
with check (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_settings.league_id and lm.user_id = auth.uid() and lm.role in ('commissioner','host','admin')));

drop policy if exists league_ai_settings_commissioner_update on public.league_ai_settings;
create policy league_ai_settings_commissioner_update on public.league_ai_settings
for update to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_settings.league_id and lm.user_id = auth.uid() and lm.role in ('commissioner','host','admin')))
with check (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_settings.league_id and lm.user_id = auth.uid() and lm.role in ('commissioner','host','admin')));

grant select on public.league_ai_settings to authenticated;
grant insert, update on public.league_ai_settings to authenticated;

commit;
