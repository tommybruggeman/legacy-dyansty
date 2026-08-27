\set ON_ERROR_STOP on

-- Start from the existing authenticated canonical-state fixture.
\ir 20261030_authenticated_canonical_team_state_read_test.sql

-- Apply the new forward-only authorization behavior.
\ir ../migrations/20261102_leaguewide_teams_tab_read.sql

do $$
declare
  r jsonb;
begin
  -- Normal league member.
  perform set_config(
    'test.actor',
    '40000000-0000-0000-0000-000000000001',
    true
  );

  -- No team requested = league-wide Teams-tab read.
  r := public.read_canonical_team_state_authenticated(
    '{
      "league_id":"00000000-0000-0000-0000-000000000001",
      "season":2026
    }'::jsonb
  );

  if jsonb_array_length(r->'teams') <> 2 then
    raise exception
      'league-wide member read failed: expected 2 teams, got %',
      jsonb_array_length(r->'teams');
  end if;

  if r->'scope_team_id' <> 'null'::jsonb then
    raise exception
      'league-wide member scope should be null: %',
      r->'scope_team_id';
  end if;

  -- Explicit own-team request must still work.
  r := public.read_canonical_team_state_authenticated(
    '{
      "league_id":"00000000-0000-0000-0000-000000000001",
      "season":2026,
      "league_team_id":"10000000-0000-0000-0000-000000000001"
    }'::jsonb
  );

  if jsonb_array_length(r->'teams') <> 1 then
    raise exception
      'explicit own-team read failed: %',
      r;
  end if;

  -- Explicit request for another owner's team must remain forbidden.
  begin
    perform public.read_canonical_team_state_authenticated(
      '{
        "league_id":"00000000-0000-0000-0000-000000000001",
        "season":2026,
        "league_team_id":"10000000-0000-0000-0000-000000000002"
      }'::jsonb
    );

    raise exception 'cross-team explicit read unexpectedly passed';

  exception
    when others then
      if sqlerrm = 'cross-team explicit read unexpectedly passed' then
        raise;
      end if;

      if sqlerrm <> 'team_state_cross_team_forbidden' then
        raise exception
          'unexpected cross-team error: %',
          sqlerrm;
      end if;
  end;

  -- Commissioner still receives league-wide state.
  perform set_config(
    'test.actor',
    '40000000-0000-0000-0000-000000000002',
    true
  );

  r := public.read_canonical_team_state_authenticated(
    '{
      "league_id":"00000000-0000-0000-0000-000000000001",
      "season":2026
    }'::jsonb
  );

  if jsonb_array_length(r->'teams') <> 2 then
    raise exception 'commissioner league-wide read failed';
  end if;

  -- Anonymous access remains forbidden.
  perform set_config('test.actor', '', true);

  begin
    perform public.read_canonical_team_state_authenticated(
      '{
        "league_id":"00000000-0000-0000-0000-000000000001",
        "season":2026
      }'::jsonb
    );

    raise exception 'anonymous read unexpectedly passed';

  exception
    when others then
      if sqlerrm = 'anonymous read unexpectedly passed' then
        raise;
      end if;
  end;
end
$$;

select '20261102 league-wide Teams tab read: PASS' as result;
