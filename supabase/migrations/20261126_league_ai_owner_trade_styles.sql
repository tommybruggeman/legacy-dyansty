-- Each owner's revealed trade lean, learned by the weekly backtest over every completed trade in the league.
-- Derived only from public Sleeper trade history, so every league member may read it. Written by the service role.
begin;

create table if not exists public.league_ai_owner_trade_styles (
  league_id uuid not null references public.leagues(id) on delete cascade,
  owner_name text not null,
  now_weight numeric not null,                 -- 0.2 future-first ... 0.8 this-season-first
  lean text check (lean in ('contending','middle','rebuilding')),
  trades integer not null default 0,
  computed_at timestamptz not null default now(),
  primary key (league_id, owner_name)
);

alter table public.league_ai_owner_trade_styles enable row level security;

drop policy if exists league_ai_owner_trade_styles_select on public.league_ai_owner_trade_styles;
create policy league_ai_owner_trade_styles_select on public.league_ai_owner_trade_styles for select to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_owner_trade_styles.league_id and lm.user_id = auth.uid()));

grant select on public.league_ai_owner_trade_styles to authenticated;

commit;
