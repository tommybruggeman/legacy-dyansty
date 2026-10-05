-- League AI learning loop: the AI logs its checkable calls, a weekly job grades them against
-- what actually happened, diagnoses the misses, and distills lessons it reads in every chat.
begin;

-- Every checkable call the AI makes (start/sit, pickup, trade verdict).
create table if not exists public.league_ai_predictions (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  user_id uuid not null default auth.uid(),
  league_team_id uuid,
  conversation_id uuid,
  kind text not null check (kind in ('start_sit','pickup','trade')),
  season integer not null,
  week integer not null,                          -- first NFL week the call applies to
  horizon_weeks integer not null default 1 check (horizon_weeks between 1 and 8),
  pick jsonb not null,                            -- [{sleeper_id, name, position}] the AI favored (trade: what you'd receive)
  over jsonb not null default '[]'::jsonb,        -- the alternatives it passed on (trade: what you'd send)
  verdict text,                                   -- trade only: 'accept' or 'decline'
  question text,
  rationale text,
  factors jsonb not null default '{}'::jsonb,     -- {"recent_usage": "major", "matchup": "minor", ...}
  confidence numeric check (confidence is null or (confidence >= 0 and confidence <= 1)),
  status text not null default 'open' check (status in ('open','graded','void','ungradeable')),
  pick_points numeric,
  over_points numeric,
  correct boolean,
  margin numeric,
  result jsonb,                                   -- per-player points and the game facts the grader saw
  cause text,                                     -- fluke, usage_shift, injury, game_script, weather, matchup, scheme, missing_info, reasoning_error
  process_error boolean,                          -- true when the reasoning was wrong, not just the outcome
  diagnosis text,
  lesson text,
  created_at timestamptz not null default now(),
  graded_at timestamptz
);
create index if not exists league_ai_predictions_open_idx on public.league_ai_predictions (status, season, week);
create index if not exists league_ai_predictions_owner_idx on public.league_ai_predictions (league_id, user_id, created_at desc);

-- Distilled lessons the AI reads before every answer. Written only by the weekly job (service role).
-- Lessons are about method ("recent target share beat matchup for WRs"), never about an owner's plans.
create table if not exists public.league_ai_lessons (
  id uuid primary key default gen_random_uuid(),
  league_id uuid not null references public.leagues(id) on delete cascade,
  kind text,                                      -- start_sit, pickup, trade, or null for general
  factor text,                                    -- the factor it is about (recent_usage, matchup, weather, ...)
  lesson text not null check (length(lesson) between 5 and 600),
  evidence_count integer not null default 1,
  hits integer not null default 0,                -- graded calls that followed this lesson and were right
  misses integer not null default 0,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists league_ai_lessons_active_idx on public.league_ai_lessons (league_id, active, evidence_count desc);

-- League-wide report card the weekly job computes (anonymous totals; no owner's individual calls).
create table if not exists public.league_ai_scorecards (
  league_id uuid not null references public.leagues(id) on delete cascade,
  season integer not null,
  computed_at timestamptz not null default now(),
  stats jsonb not null,                           -- {"by_kind": {...}, "by_factor": {...}, "by_cause": {...}}
  primary key (league_id, season)
);

-- NFL game conditions for the diagnostic: final score, Vegas lines, roof, weather, coaches.
create table if not exists public.nfl_games (
  game_id text primary key,
  season integer not null,
  game_type text,
  week integer not null,
  gameday date,
  gametime text,
  away_team text, home_team text,
  away_score integer, home_score integer,
  spread_line numeric, total_line numeric,
  roof text, surface text,
  temp numeric, wind numeric,
  away_coach text, home_coach text,
  away_qb_name text, home_qb_name text,
  stadium text,
  div_game boolean,
  refreshed_at timestamptz not null default now()
);
create index if not exists nfl_games_season_week_idx on public.nfl_games (season, week);

-- Every model call: tokens, list-price cost, model, effort, tools. Feeds the weekly report.
create table if not exists public.league_ai_usage (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  league_id uuid references public.leagues(id) on delete cascade,
  user_id uuid,                                   -- the owner who asked (null for background work)
  feature text not null,                          -- chat, brief, grading, grading_review, playbook
  model text not null,
  effort text,
  topic text,                                     -- chat only: trade, lineup, waivers, contracts, draft, strategy, lookup, other
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  cache_read_tokens integer not null default 0,
  cache_write_tokens integer not null default 0,
  web_searches integer not null default 0,
  tool_calls jsonb not null default '[]'::jsonb,
  rounds integer not null default 0,
  latency_ms integer not null default 0,
  batch boolean not null default false,
  ok boolean not null default true,
  cost_usd numeric,
  conversation_id uuid,
  prediction_id uuid
);
create index if not exists league_ai_usage_league_time_idx on public.league_ai_usage (league_id, created_at desc);

-- The weekly report the job writes (and emails). Commissioners read it in the app.
create table if not exists public.league_ai_reports (
  league_id uuid not null references public.leagues(id) on delete cascade,
  week_start date not null,
  week_end date not null,
  report jsonb not null,
  html text,
  created_at timestamptz not null default now(),
  primary key (league_id, week_start)
);

alter table public.league_ai_usage enable row level security;
alter table public.league_ai_reports enable row level security;

drop policy if exists league_ai_usage_insert_own on public.league_ai_usage;
create policy league_ai_usage_insert_own on public.league_ai_usage for insert to authenticated
with check (user_id = auth.uid() and exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_usage.league_id and lm.user_id = auth.uid()));
drop policy if exists league_ai_usage_select_own on public.league_ai_usage;
create policy league_ai_usage_select_own on public.league_ai_usage for select to authenticated using (user_id = auth.uid());
grant select, insert on public.league_ai_usage to authenticated;
grant usage, select on sequence public.league_ai_usage_id_seq to authenticated;

drop policy if exists league_ai_reports_commissioner on public.league_ai_reports;
create policy league_ai_reports_commissioner on public.league_ai_reports for select to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_reports.league_id and lm.user_id = auth.uid()
               and lower(lm.role) in ('commissioner','admin','host')));
grant select on public.league_ai_reports to authenticated;

alter table public.league_ai_predictions enable row level security;
alter table public.league_ai_lessons enable row level security;
alter table public.league_ai_scorecards enable row level security;
alter table public.nfl_games enable row level security;

-- predictions: an owner logs and reads only their own; grading runs as the service role
drop policy if exists league_ai_predictions_select on public.league_ai_predictions;
create policy league_ai_predictions_select on public.league_ai_predictions for select to authenticated
using (user_id = auth.uid());
drop policy if exists league_ai_predictions_insert on public.league_ai_predictions;
create policy league_ai_predictions_insert on public.league_ai_predictions for insert to authenticated
with check (
  user_id = auth.uid()
  and status = 'open'
  and exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_predictions.league_id and lm.user_id = auth.uid())
);
-- an owner may only void their own open call (when the AI reverses itself); grades are written by the service role
drop policy if exists league_ai_predictions_void on public.league_ai_predictions;
create policy league_ai_predictions_void on public.league_ai_predictions for update to authenticated
using (user_id = auth.uid() and status = 'open')
with check (user_id = auth.uid() and status = 'void');

-- lessons and scorecards: any league member reads
drop policy if exists league_ai_lessons_member_read on public.league_ai_lessons;
create policy league_ai_lessons_member_read on public.league_ai_lessons for select to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_lessons.league_id and lm.user_id = auth.uid()));
drop policy if exists league_ai_scorecards_member_read on public.league_ai_scorecards;
create policy league_ai_scorecards_member_read on public.league_ai_scorecards for select to authenticated
using (exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_scorecards.league_id and lm.user_id = auth.uid()));

drop policy if exists nfl_games_authenticated_read on public.nfl_games;
create policy nfl_games_authenticated_read on public.nfl_games for select to authenticated using (true);

grant select, insert on public.league_ai_predictions to authenticated;
grant update (status) on public.league_ai_predictions to authenticated;
grant select on public.league_ai_lessons to authenticated;
grant select on public.league_ai_scorecards to authenticated;
grant select on public.nfl_games to authenticated;

commit;
