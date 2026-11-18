-- Our own player status (api/player_status.py): the confirmed status per player, refreshed every 5 minutes on the
-- watcher VM from ESPN's public injury report, and a log of every change (for status-change alerts and grading).
begin;

create table if not exists public.nfl_player_status (
  sleeper_id text primary key,
  espn_id text,
  status text not null default 'Healthy',   -- Healthy, Questionable, Doubtful, Out, IR, PUP, NFI, Sus, DTD
  body_part text,
  pending text,                             -- a new reading waiting for a second look
  pending_since timestamptz,
  since timestamptz,                        -- when the current status was confirmed
  checked_at timestamptz not null default now()
);
create index if not exists nfl_player_status_espn_idx on public.nfl_player_status (espn_id);

create table if not exists public.nfl_status_changes (
  id bigint generated always as identity primary key,
  sleeper_id text not null,
  from_status text not null,
  to_status text not null,
  body_part text,
  source text not null default 'espn',
  changed_at timestamptz not null default now()
);
create index if not exists nfl_status_changes_player_idx on public.nfl_status_changes (sleeper_id, changed_at desc);
create index if not exists nfl_status_changes_time_idx on public.nfl_status_changes (changed_at desc);

alter table public.nfl_player_status enable row level security;
alter table public.nfl_status_changes enable row level security;
do $$
declare t text;
begin
  foreach t in array array['nfl_player_status','nfl_status_changes'] loop
    execute format('drop policy if exists %I on public.%I', t||'_authenticated_read', t);
    execute format('create policy %I on public.%I for select to authenticated using (true)', t||'_authenticated_read', t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end$$;

commit;
