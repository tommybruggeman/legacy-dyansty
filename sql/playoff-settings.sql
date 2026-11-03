-- Our own playoff format (Matchups bracket, app-run playoffs). Safe to run more than once.
alter table public.league_ai_settings add column if not exists playoff_week_start integer not null default 15;
alter table public.league_ai_settings add column if not exists playoff_round_weeks integer not null default 2;
alter table public.league_ai_settings add column if not exists loser_bracket boolean not null default true;
