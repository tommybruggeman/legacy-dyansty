-- Push alerts: each owner's phones (Expo push tokens) and which league alerts have gone out.
-- Owners register and see only their own phones; the alert job (service role) reads every token in a league.
begin;

create table if not exists public.push_tokens (
  token text primary key,
  user_id uuid not null default auth.uid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  platform text,
  updated_at timestamptz not null default now()
);
create index if not exists push_tokens_league_idx on public.push_tokens (league_id);

alter table public.push_tokens enable row level security;

drop policy if exists push_tokens_select on public.push_tokens;
create policy push_tokens_select on public.push_tokens for select to authenticated
using (user_id = auth.uid());

drop policy if exists push_tokens_insert on public.push_tokens;
create policy push_tokens_insert on public.push_tokens for insert to authenticated
with check (
  user_id = auth.uid()
  and exists (select 1 from public.league_memberships lm where lm.league_id = push_tokens.league_id and lm.user_id = auth.uid())
);

drop policy if exists push_tokens_update on public.push_tokens;
create policy push_tokens_update on public.push_tokens for update to authenticated
using (user_id = auth.uid())
with check (
  user_id = auth.uid()
  and exists (select 1 from public.league_memberships lm where lm.league_id = push_tokens.league_id and lm.user_id = auth.uid())
);

drop policy if exists push_tokens_delete on public.push_tokens;
create policy push_tokens_delete on public.push_tokens for delete to authenticated
using (user_id = auth.uid());

grant select, insert, update, delete on public.push_tokens to authenticated;

-- One row per alert sent, so no trade is announced twice. Service role only.
create table if not exists public.push_sent (
  league_id uuid not null references public.leagues(id) on delete cascade,
  key text not null,
  sent_at timestamptz not null default now(),
  primary key (league_id, key)
);
alter table public.push_sent enable row level security;

commit;
