-- Sleeper sync control: per-league pause switch, replay watermark, and the
-- exception log the commissioner reviews when a transaction cannot be applied.
begin;
set local search_path = pg_catalog, public;

create table if not exists public.league_sleeper_sync (
    league_id uuid primary key references public.leagues(id) on delete cascade,
    -- Starts disabled on purpose: a league must be switched on deliberately,
    -- never by the mere existence of a config row.
    sync_enabled boolean not null default false,
    pause_reason text,
    paused_at timestamptz,
    paused_by uuid,
    -- Sleeper's own `created` epoch-ms on the newest applied transaction.
    -- Resuming advances this to now, so a pause window is never replayed.
    watermark_created_ms bigint not null default 0,
    watermark_transaction_id text,
    last_run_at timestamptz,
    last_run_status text check (
        last_run_status is null
        or last_run_status in ('ok', 'skipped_disabled', 'error')
    ),
    last_run_detail text,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp()
);

create table if not exists public.sleeper_sync_exceptions (
    id uuid primary key default gen_random_uuid(),
    league_id uuid not null references public.leagues(id) on delete cascade,
    sleeper_transaction_id text not null,
    player_id text,
    sleeper_roster_id integer,
    league_team_id uuid references public.league_teams(id),
    exception_kind text not null check (exception_kind in (
        'no_live_contract',
        'duplicate_live_contract',
        'unmapped_roster',
        'ambiguous_contract',
        'cap_overage',
        'unsupported_transaction'
    )),
    detail text not null,
    payload jsonb,
    status text not null default 'open' check (status in ('open', 'resolved', 'ignored')),
    resolved_at timestamptz,
    resolved_by uuid,
    created_at timestamptz not null default clock_timestamp(),
    -- One row per problem per transaction, so a re-run never piles up duplicates.
    unique (league_id, sleeper_transaction_id, exception_kind, player_id)
);

create index if not exists sleeper_sync_exceptions_open_idx
    on public.sleeper_sync_exceptions (league_id, status, created_at desc);

alter table public.league_sleeper_sync enable row level security;
alter table public.sleeper_sync_exceptions enable row level security;

-- Any league member may see whether the sync is running and what it flagged.
drop policy if exists league_sleeper_sync_select on public.league_sleeper_sync;
create policy league_sleeper_sync_select on public.league_sleeper_sync
for select to authenticated
using (
    exists (
        select 1 from public.league_memberships m
         where m.league_id = league_sleeper_sync.league_id
           and m.user_id = auth.uid()
    )
);

-- Only the commissioner may pause, resume, or move the watermark.
drop policy if exists league_sleeper_sync_write on public.league_sleeper_sync;
create policy league_sleeper_sync_write on public.league_sleeper_sync
for all to authenticated
using (
    exists (
        select 1 from public.league_memberships m
         where m.league_id = league_sleeper_sync.league_id
           and m.user_id = auth.uid()
           and lower(m.role) in ('commissioner', 'admin', 'host')
    )
)
with check (
    exists (
        select 1 from public.league_memberships m
         where m.league_id = league_sleeper_sync.league_id
           and m.user_id = auth.uid()
           and lower(m.role) in ('commissioner', 'admin', 'host')
    )
);

drop policy if exists sleeper_sync_exceptions_select on public.sleeper_sync_exceptions;
create policy sleeper_sync_exceptions_select on public.sleeper_sync_exceptions
for select to authenticated
using (
    exists (
        select 1 from public.league_memberships m
         where m.league_id = sleeper_sync_exceptions.league_id
           and m.user_id = auth.uid()
    )
);

drop policy if exists sleeper_sync_exceptions_write on public.sleeper_sync_exceptions;
create policy sleeper_sync_exceptions_write on public.sleeper_sync_exceptions
for all to authenticated
using (
    exists (
        select 1 from public.league_memberships m
         where m.league_id = sleeper_sync_exceptions.league_id
           and m.user_id = auth.uid()
           and lower(m.role) in ('commissioner', 'admin', 'host')
    )
)
with check (
    exists (
        select 1 from public.league_memberships m
         where m.league_id = sleeper_sync_exceptions.league_id
           and m.user_id = auth.uid()
           and lower(m.role) in ('commissioner', 'admin', 'host')
    )
);

-- Seed a disabled row for every league that already has a Sleeper connection.
insert into public.league_sleeper_sync (league_id, sync_enabled, pause_reason)
select l.id, false, 'Not yet enabled'
  from public.leagues l
 where l.sleeper_league_id is not null
on conflict (league_id) do nothing;

commit;
