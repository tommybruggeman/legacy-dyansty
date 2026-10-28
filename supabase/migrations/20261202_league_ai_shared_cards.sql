-- League-wide AI cards: the weekly start/sit scorecard and the accepted-trade shares are written by the weekly job
-- (service role, system user id) and read by every member of that league. Each owner's own scorecard line is saved
-- under their own user id, so the existing own-rows policy keeps it private.
drop policy if exists league_ai_experiments_league_cards on public.league_ai_experiments;
create policy league_ai_experiments_league_cards on public.league_ai_experiments for select to authenticated
using (
  experiment in ('start_sit_scorecard_v1', 'trade_accept_curve_v1')
  and exists (select 1 from public.league_memberships lm where lm.league_id = league_ai_experiments.league_id and lm.user_id = auth.uid())
);
