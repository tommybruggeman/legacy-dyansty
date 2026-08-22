create table if not exists player_decision_context (
    id bigserial primary key,
    sleeper_id text,
    player_name text not null,
    pos text,
    current_owner text,
    nfl_team text,

    salary numeric default 0,
    years numeric default 0,

    past_production_score numeric default 0,
    current_production_score numeric default 0,
    future_projection_score numeric default 0,
    role_score numeric default 0,
    situation_score numeric default 0,
    career_arc_score numeric default 0,
    contract_score numeric default 0,
    dynasty_score numeric default 0,
    trade_value_score numeric default 0,
    risk_score numeric default 0,

    win_now_score numeric default 0,
    long_term_build_score numeric default 0,
    rebuild_core_score numeric default 0,
    weekly_start_score numeric default 0,
    contract_decision_score numeric default 0,
    sell_risk_score numeric default 0,
    hold_score numeric default 0,

    career_trajectory text,
    team_role text,
    decision_tier text,
    decision_summary text,

    updated_at timestamptz default now(),

    unique (sleeper_id, current_owner)
);
