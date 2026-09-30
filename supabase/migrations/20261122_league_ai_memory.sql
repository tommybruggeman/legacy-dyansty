-- League AI memory: private per-owner notes, a shared league notebook, and saved conversations.
begin;

create table if not exists public.league_ai_memory (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  scope text not null check (scope in ('owner','league')),
  user_id uuid,                                  -- set for scope = owner
  league_team_id uuid,
  note text not null check (length(note) between 3 and 600),
  created_by uuid not null default auth.uid(),
  created_at timestamptz not null default now(),
  active boolean not null default true
);
create index if not exists league_ai_memory_owner_idx on public.league_ai_memory (league_id, user_id, active, created_at desc);
create index if not exists league_ai_memory_league_idx on public.league_ai_memory (league_id, scope, active, created_at desc);

create table if not exists public.league_ai_conversations (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  user_id uuid not null,
  league_team_id uuid,
  title text,
  messages jsonb not null default '[]'::jsonb,   -- [{role, content}]
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists league_ai_conversations_user_idx on public.league_ai_conversations (league_id, user_id, updated_at desc);

alter table public.league_ai_memory enable row level security;
alter table public.league_ai_conversations enable row level security;

-- owner notes: only that user; league notes: any member reads, any member writes (as themselves)
drop policy if exists league_ai_memory_select on public.league_ai_memory;
create policy league_ai_memory_select on public.league_ai_memory for select to authenticated
using (
  (scope = 'owner' and user_id = auth.uid())
  or (scope = 'league' and exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_memory.league_id and lm.user_id = auth.uid()))
);
drop policy if exists league_ai_memory_insert on public.league_ai_memory;
create policy league_ai_memory_insert on public.league_ai_memory for insert to authenticated
with check (
  created_by = auth.uid()
  and exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_memory.league_id and lm.user_id = auth.uid())
  and (scope = 'league' or user_id = auth.uid())
);
drop policy if exists league_ai_memory_update on public.league_ai_memory;
create policy league_ai_memory_update on public.league_ai_memory for update to authenticated
using (created_by = auth.uid()) with check (created_by = auth.uid());

drop policy if exists league_ai_conversations_own on public.league_ai_conversations;
create policy league_ai_conversations_own on public.league_ai_conversations for all to authenticated
using (user_id = auth.uid()) with check (user_id = auth.uid());

grant select, insert, update on public.league_ai_memory to authenticated;
grant select, insert, update, delete on public.league_ai_conversations to authenticated;

commit;
