-- League history for League AI: every transaction, draft pick and matchup, all seasons. Members read.
begin;

create table if not exists public.league_history_events (
  league_id uuid not null references public.leagues(id) on delete cascade,
  event_id text not null,                 -- source-stable id (sleeper transaction id + leg, draft pick id, canonical event id)
  kind text not null,                     -- trade | add | drop | waiver | draft_pick | commissioner
  season integer,
  week integer,
  occurred_at timestamptz,
  owner_name text,                        -- team acting / receiving
  counterparty_names text[],              -- other owners in a trade
  player_name text,
  player_id text,                         -- sleeper id
  position text,
  faab_bid numeric,                       -- waiver price when known
  contract_salary numeric,                -- from the app's contract record when matched
  contract_years integer,
  details jsonb not null default '{}'::jsonb,
  summary text,                           -- one-line, human readable
  source text not null,
  refreshed_at timestamptz not null default now(),
  primary key (league_id, event_id)
);
create index if not exists league_history_events_league_season_idx on public.league_history_events (league_id, season desc, week desc);
create index if not exists league_history_events_owner_idx on public.league_history_events (league_id, owner_name);
create index if not exists league_history_events_player_idx on public.league_history_events (league_id, player_id);

create table if not exists public.league_matchups (
  league_id uuid not null references public.leagues(id) on delete cascade,
  season integer not null,
  week integer not null,
  owner_name text not null,
  opponent_name text,
  points numeric,
  opponent_points numeric,
  won boolean,
  top_scorer_bonus boolean,               -- finished in the top N scorers that week
  standing_points numeric,
  is_playoff boolean default false,
  source text not null default 'sleeper',
  refreshed_at timestamptz not null default now(),
  primary key (league_id, season, week, owner_name)
);
create index if not exists league_matchups_owner_idx on public.league_matchups (league_id, owner_name, season);

alter table public.league_history_events enable row level security;
alter table public.league_matchups enable row level security;
do $$
declare t text;
begin
  foreach t in array array['league_history_events','league_matchups'] loop
    execute format('drop policy if exists %I on public.%I', t||'_member_select', t);
    execute format('create policy %I on public.%I for select to authenticated using (exists (select 1 from public.league_memberships lm where lm.league_id = %I.league_id and lm.user_id = auth.uid()))', t||'_member_select', t, t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end$$;

commit;
