-- Lets every league member read the league-wide AI cards, now including the weekly projection accuracy card
-- (projection_calibration_v1) and the pregame projection snapshots (projection_snapshot_v1). Same rule as before:
-- rows are written by the daily job and readable only by members of that league.
drop policy if exists league_ai_experiments_league_cards on public.league_ai_experiments;
create policy league_ai_experiments_league_cards on public.league_ai_experiments for select to authenticated
using (
  experiment in ('start_sit_scorecard_v1', 'trade_accept_curve_v1', 'projection_calibration_v1', 'projection_snapshot_v1')
  and exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_experiments.league_id and lm.user_id = auth.uid())
);
