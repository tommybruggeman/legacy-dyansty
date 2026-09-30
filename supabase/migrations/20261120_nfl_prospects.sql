-- College prospects for rookie-draft planning (CollegeFootballData). Members read.
begin;

create table if not exists public.nfl_prospects (
  cfbd_athlete_id text not null,
  season integer not null,
  name text not null,
  search_name text,
  position text,
  college text,
  conference text,
  class_year integer,            -- 1 Fr, 2 So, 3 Jr, 4 Sr (CFBD roster 'year')
  draft_eligible boolean,        -- class_year >= 3 in that season
  height numeric,
  weight numeric,
  recruit_stars integer,
  recruit_rating numeric,
  recruit_rank integer,
  recruit_year integer,
  usage_overall numeric,         -- share of team plays involving the player
  usage_pass numeric,
  usage_rush numeric,
  pass_att numeric, pass_yds numeric, pass_td numeric, pass_int numeric,
  rush_att numeric, rush_yds numeric, rush_td numeric,
  receptions numeric, rec_yds numeric, rec_td numeric,
  scrimmage_yds numeric,
  nfl_draft_year integer, nfl_draft_round integer, nfl_draft_pick integer, nfl_team text,
  sleeper_id text,
  source text not null default 'cfbd',
  refreshed_at timestamptz not null default now(),
  primary key (cfbd_athlete_id, season)
);
create index if not exists nfl_prospects_season_pos_idx on public.nfl_prospects (season, position, scrimmage_yds desc);
create index if not exists nfl_prospects_search_idx on public.nfl_prospects (search_name);

alter table public.nfl_prospects enable row level security;
drop policy if exists nfl_prospects_authenticated_read on public.nfl_prospects;
create policy nfl_prospects_authenticated_read on public.nfl_prospects for select to authenticated using (true);
grant select on public.nfl_prospects to authenticated;

commit;
