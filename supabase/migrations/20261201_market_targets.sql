-- Transfer targets: players an owner saves (☆) on the app's Market page.
-- Private: each owner reads and changes only their own list. The Front Office uses it to plan the offseason.
begin;

create table if not exists public.market_targets (
  league_id uuid not null references public.leagues(id) on delete cascade,
  user_id uuid not null default auth.uid(),
  player_id text not null,
  created_at timestamptz not null default now(),
  primary key (league_id, user_id, player_id)
);

alter table public.market_targets enable row level security;

drop policy if exists market_targets_select on public.market_targets;
create policy market_targets_select on public.market_targets for select to authenticated
using (user_id = auth.uid());

drop policy if exists market_targets_insert on public.market_targets;
create policy market_targets_insert on public.market_targets for insert to authenticated
with check (
  user_id = auth.uid()
  and exists (select 1 from public.league_memberships lm where lm.league_id = market_targets.league_id and lm.user_id = auth.uid())
);

drop policy if exists market_targets_delete on public.market_targets;
create policy market_targets_delete on public.market_targets for delete to authenticated
using (user_id = auth.uid());

grant select, insert, delete on public.market_targets to authenticated;

commit;
