drop table if exists team_future_context;

create table team_future_context (
  league_id uuid not null,
  owner_id text not null,
  owner_team_name text,

  roster_count int,
  avg_age numeric,
  avg_contract_years numeric,
  avg_salary numeric,

  young_core_count int,
  aging_risk_count int,
  draft_pick_count int,
  premium_pick_count int,

  cap_space numeric,
  cap_used numeric,

  team_dynasty_asset_score numeric,
  team_win_now_score numeric,
  team_contract_value_score numeric,
  team_risk_score numeric,

  age_score numeric,
  pick_score numeric,
  cap_score numeric,
  core_score numeric,

  future_score numeric,
  future_grade text,
  team_window text,

  updated_at timestamptz default now(),

  primary key (league_id, owner_id)
);
