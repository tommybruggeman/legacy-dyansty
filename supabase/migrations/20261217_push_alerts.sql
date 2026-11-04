-- Push alerts straight to Apple (no Expo push service): phone tokens say what kind they are, each owner has
-- on/off switches per alert kind, and every alert goes through one outbox so nothing is sent twice.
begin;

-- 1. Phones: APNs device tokens from the app (kind 'apns'); older rows are Expo tokens and are no longer used.
alter table public.push_tokens add column if not exists kind text not null default 'expo';
alter table public.push_tokens add column if not exists tz text;

-- 2. Each owner's switches (the kinds they turned off) and time zone for quiet hours. Owners read and write only their own.
create table if not exists public.push_prefs (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  league_id uuid not null references public.leagues(id) on delete cascade,
  off text[] not null default '{}',
  tz text,
  updated_at timestamptz not null default now(),
  primary key (user_id, league_id)
);
alter table public.push_prefs enable row level security;
drop policy if exists push_prefs_own on public.push_prefs;
create policy push_prefs_own on public.push_prefs for all to authenticated
  using (user_id = auth.uid())
  with check (
    user_id = auth.uid()
    and exists (select 1 from public.league_memberships lm where lm.league_id = push_prefs.league_id and lm.user_id = auth.uid())
  );
grant select, insert, update, delete on public.push_prefs to authenticated;

-- 3. The outbox: one row per owner per alert (unique key per owner). The alert job (service role) fills and sends it.
create table if not exists public.push_outbox (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null,
  key text not null,
  title text not null default '',
  body text not null default '',
  url text,
  urgent boolean not null default false,
  not_before timestamptz not null default now(),
  expires_at timestamptz,
  status text not null default 'queued'
    check (status in ('queued','sent','seeded','off','expired','limited','no_phone','failed')),
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  unique (user_id, key)
);
create index if not exists push_outbox_due_idx on public.push_outbox (status, not_before);
create index if not exists push_outbox_user_sent_idx on public.push_outbox (user_id, sent_at);
alter table public.push_outbox enable row level security;
grant select, insert, update on public.push_outbox to service_role;
-- Owners can see their own alerts, and queue a test alert for themselves (Settings > Alerts > Send me a test).
drop policy if exists push_outbox_own_read on public.push_outbox;
create policy push_outbox_own_read on public.push_outbox for select to authenticated using (user_id = auth.uid());
drop policy if exists push_outbox_own_test on public.push_outbox;
create policy push_outbox_own_test on public.push_outbox for insert to authenticated
  with check (user_id = auth.uid() and kind = 'test' and status = 'queued'
              and exists (select 1 from public.league_memberships lm where lm.league_id = push_outbox.league_id and lm.user_id = auth.uid()));
grant select, insert on public.push_outbox to authenticated;

commit;
