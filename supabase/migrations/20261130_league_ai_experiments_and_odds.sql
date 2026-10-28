-- 1) AI experiments: a self-check logged after an answer, to decide whether a new input is worth keeping.
-- First use: 'playoff_odds_v1'. After each of the next 20 answers that used the playoff-odds simulation,
-- a small model compares the answer with the one it would have given without the odds.
begin;

create table if not exists public.league_ai_experiments (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  user_id uuid not null default auth.uid(),
  experiment text not null,
  conversation_id uuid,
  question text,
  result jsonb not null default '{}'::jsonb,   -- changed, without_answer, better, why, risk, the odds the AI saw
  cost_usd numeric,
  created_at timestamptz not null default now()
);
create index if not exists league_ai_experiments_idx on public.league_ai_experiments (league_id, experiment, created_at desc);

alter table public.league_ai_experiments enable row level security;

-- private like the chats: an owner reads and writes only their own rows; the weekly report reads all with the service role
drop policy if exists league_ai_experiments_select on public.league_ai_experiments;
create policy league_ai_experiments_select on public.league_ai_experiments for select to authenticated
using (user_id = auth.uid());
drop policy if exists league_ai_experiments_insert on public.league_ai_experiments;
create policy league_ai_experiments_insert on public.league_ai_experiments for insert to authenticated
with check (
  user_id = auth.uid()
  and exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_experiments.league_id and lm.user_id = auth.uid())
);

-- How many checks a league has logged for an experiment (league-wide, without exposing anyone's rows).
create or replace function public.league_ai_experiment_count(p_league_id uuid, p_experiment text)
returns integer
language sql
security definer
set search_path = public
as $$
  select count(*)::integer from public.league_ai_experiments
  where league_id = p_league_id and experiment = p_experiment
    and exists (select 1 from public.league_memberships lm where lm.league_id = p_league_id and lm.user_id = auth.uid());
$$;
grant execute on function public.league_ai_experiment_count(uuid, text) to authenticated;

-- 2) Saved odds: each week's playoff odds and matchup win odds for every team, graded once the games are final.
-- League-level numbers every member sees on the Front Office screen, so any member can read them. The first
-- snapshot of the week is kept (later writes that week are ignored), so the odds graded are the pre-game ones.
create table if not exists public.league_ai_odds_history (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  season integer not null,
  week integer not null,
  owner_name text not null,
  playoff_pct numeric,
  win_pct numeric,
  mu numeric,          -- expected points this week
  var numeric,         -- variance of that total
  opp_owner text,
  opp_mu numeric,
  opp_var numeric,
  created_at timestamptz not null default now(),
  unique (league_id, season, week, owner_name)
);

alter table public.league_ai_odds_history enable row level security;

drop policy if exists league_ai_odds_history_select on public.league_ai_odds_history;
create policy league_ai_odds_history_select on public.league_ai_odds_history for select to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_odds_history.league_id and lm.user_id = auth.uid()));
drop policy if exists league_ai_odds_history_insert on public.league_ai_odds_history;
create policy league_ai_odds_history_insert on public.league_ai_odds_history for insert to authenticated
with check (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_odds_history.league_id and lm.user_id = auth.uid()));

commit;
