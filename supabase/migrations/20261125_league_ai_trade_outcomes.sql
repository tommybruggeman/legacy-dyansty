-- Trade outcomes: whether a trade the AI pitched or judged actually happened.
-- Sleeper's public data shows only completed trades, so outcomes come from three places:
--   sleeper  : the nightly learning job matched a completed Sleeper trade (accepted)
--   inferred : the job saw no matching trade within 10 days (not_completed, a soft no)
--   chat     : the owner told the AI how the other owner answered (declined / countered / accepted)
begin;

alter table public.league_ai_predictions add column if not exists partner text;  -- trade calls: the other owner

create table if not exists public.league_ai_trade_outcomes (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  user_id uuid not null default auth.uid(),
  prediction_id uuid references public.league_ai_predictions(id) on delete set null,
  partner text,
  outcome text not null check (outcome in ('accepted','declined','countered','not_completed')),
  source text not null check (source in ('sleeper','inferred','chat')),
  receives jsonb not null default '[]'::jsonb,      -- [{sleeper_id, name}] the asking owner would get
  sends jsonb not null default '[]'::jsonb,         -- what they would send
  note text,                                        -- the other owner's reason or counter, in the owner's words
  sleeper_transaction_id text,
  occurred_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists league_ai_trade_outcomes_owner_idx on public.league_ai_trade_outcomes (league_id, user_id, created_at desc);
create index if not exists league_ai_trade_outcomes_prediction_idx on public.league_ai_trade_outcomes (prediction_id);

alter table public.league_ai_trade_outcomes enable row level security;

-- private like the calls themselves: an owner reads and records only their own
drop policy if exists league_ai_trade_outcomes_select on public.league_ai_trade_outcomes;
create policy league_ai_trade_outcomes_select on public.league_ai_trade_outcomes for select to authenticated
using (user_id = auth.uid());
drop policy if exists league_ai_trade_outcomes_insert on public.league_ai_trade_outcomes;
create policy league_ai_trade_outcomes_insert on public.league_ai_trade_outcomes for insert to authenticated
with check (
  user_id = auth.uid()
  and source = 'chat'
  and exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_trade_outcomes.league_id and lm.user_id = auth.uid())
);

grant select, insert on public.league_ai_trade_outcomes to authenticated;

commit;
