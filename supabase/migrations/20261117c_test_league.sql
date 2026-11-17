-- A test copy of the league ("Legacy Test") to try in-app trades, waivers, lineups and the cap rules before the real
-- switch-over, without touching the real league.
--
--   select public.create_test_league();                  -- makes the copy, returns what it copied
--   select public.delete_test_league('<test league id>'); -- removes it again
--
-- The copy:
--   * gets every row of the league's own tables (teams, contracts, picks, cap adjustments, settings, history...) with
--     new ids, so nothing is shared with the real league;
--   * renames every team "Test Team N" and owner "Owner N" (N = roster number), everywhere the old name appears;
--   * has no Sleeper league: its seasons carry a mirror id ("mirror-" + the Sleeper id written in letters) that the
--     server reads from the real league, read only (api/sleeper.py). Jobs that keep only digits skip it;
--   * gets only the commissioner as a member, so no owner sees it or gets its alerts. In Settings > League the
--     commissioner opens it and can play as any team (to make and accept trades);
--   * has the switch off. Turn it on in the test league's Settings > Commissioner, which also seeds FAAB.
begin;
set local search_path = pg_catalog, public;

alter table public.leagues add column if not exists is_test boolean not null default false;

-- Which league the app opens for each person (Settings > League). No row = their first real league.
create table if not exists public.user_active_league(
  user_id uuid primary key,
  league_id uuid not null references public.leagues(id) on delete cascade,
  updated_at timestamptz not null default clock_timestamp()
);
alter table public.user_active_league enable row level security;
revoke all on public.user_active_league from public, anon, authenticated;
grant select on public.user_active_league to authenticated;
drop policy if exists user_active_league_own_read on public.user_active_league;
create policy user_active_league_own_read on public.user_active_league for select to authenticated using (user_id = auth.uid());

create or replace function public.set_active_league_authenticated(p_league_id uuid) returns void
language plpgsql security definer set search_path = pg_catalog, public as $$
begin
  if auth.uid() is null or not exists (select 1 from public.league_memberships where league_id = p_league_id and user_id = auth.uid()) then
    raise exception 'league_not_available';
  end if;
  insert into public.user_active_league(user_id, league_id) values (auth.uid(), p_league_id)
  on conflict (user_id) do update set league_id = excluded.league_id, updated_at = clock_timestamp();
end $$;
revoke all on function public.set_active_league_authenticated(uuid) from public, anon;
grant execute on function public.set_active_league_authenticated(uuid) to authenticated;

-- Test leagues only: the commissioner plays as any team (their membership moves to it).
create or replace function public.switch_test_team_authenticated(p_league_id uuid, p_team_id uuid) returns void
language plpgsql security definer set search_path = pg_catalog, public as $$
begin
  if not coalesce((select is_test from public.leagues where id = p_league_id), false) then raise exception 'not_a_test_league'; end if;
  if not exists (select 1 from public.league_memberships where league_id = p_league_id and user_id = auth.uid() and role = 'commissioner') then
    raise exception 'Commissioner authority required';
  end if;
  if not exists (select 1 from public.league_teams where id = p_team_id and league_id = p_league_id) then raise exception 'team_invalid'; end if;
  update public.league_memberships set league_team_id = p_team_id where league_id = p_league_id and user_id = auth.uid();
end $$;
revoke all on function public.switch_test_team_authenticated(uuid, uuid) from public, anon;
grant execute on function public.switch_test_team_authenticated(uuid, uuid) to authenticated;

-- Turning the switch on: each team's FAAB left on Sleeper becomes its starting balance (teams with none yet).
-- p_rows: [{league_team_id, amount}]
create or replace function public.commissioner_seed_faab_authenticated(p_league_id uuid, p_season int, p_rows jsonb) returns int
language plpgsql security definer set search_path = pg_catalog, public as $$
declare n int;
begin
  if not public.is_league_commissioner_private(p_league_id) then raise exception 'Commissioner authority required'; end if;
  insert into public.faab_ledger(league_id, league_team_id, season, amount, reason, note)
  select p_league_id, (r->>'league_team_id')::uuid, p_season, round(greatest((r->>'amount')::numeric, 0), 2), 'start', 'FAAB left on Sleeper at the switch'
  from jsonb_array_elements(coalesce(p_rows, '[]')) r
  where exists (select 1 from public.league_teams t where t.id = (r->>'league_team_id')::uuid and t.league_id = p_league_id)
    and not exists (select 1 from public.faab_ledger f where f.league_id = p_league_id and f.season = p_season and f.reason = 'start'
                    and f.league_team_id = (r->>'league_team_id')::uuid);
  get diagnostics n = row_count;
  return n;
end $$;
revoke all on function public.commissioner_seed_faab_authenticated(uuid, int, jsonb) from public, anon;
grant execute on function public.commissioner_seed_faab_authenticated(uuid, int, jsonb) to authenticated;

-- Swaps every top-level value of a row that is a copied row's id (to its new id) or an owner/team name (to its fake).
-- Reads the _test_map / _test_names temp tables that create_test_league fills.
create or replace function public.test_league_remap_private(j jsonb) returns jsonb
language plpgsql stable set search_path = pg_catalog, public as $$
begin
  return (select coalesce(jsonb_object_agg(e.key, case when jsonb_typeof(e.value) = 'string'
           then coalesce((select to_jsonb(m.new) from _test_map m where m.old = e.value #>> '{}'),
                         (select to_jsonb(n.new) from _test_names n where n.old = e.value #>> '{}'), e.value)
           else e.value end), '{}'::jsonb)
  from jsonb_each(j) e);
end $$;

-- The league's own tables that are NOT copied: who's in it, phones, AI chats/costs/logs, the Sleeper sync, open offers
-- and bids, past rollovers. Everything else with a league_id is copied.
create or replace function public.test_league_skip_private(t text) returns boolean
language sql immutable as $$
  select t in ('leagues', 'league_memberships', 'league_membership_authority_events', 'user_active_league',
               'push_outbox', 'push_prefs', 'push_sent', 'push_tokens', 'live_activities', 'owner_profiles', 'market_targets',
               'league_sleeper_sync', 'sleeper_sync_exceptions', 'trade_offers', 'app_waiver_claims', 'cap_auto_losses',
               'league_invites', 'publication_context_generations', 'faab_ledger')
      or t like 'rollover\_%' or (t like 'league\_ai\_%' and t <> 'league_ai_settings')
$$;

create or replace function public.create_test_league(p_source uuid default null) returns jsonb
language plpgsql set search_path = pg_catalog, public as $$
declare
  src uuid := p_source;
  dst uuid := gen_random_uuid();
  tables text[];
  pending text[];
  t text;
  cols text;
  n int;
  progress boolean;
  copied jsonb := '{}';
  failed jsonb := '{}';
  errs jsonb;
  this_week int;
  extra text;
  tag text := ':test:' || left(dst::text, 8);
begin
  -- skip foreign key and guard triggers while copying, where the database allows it (rows are copied as they are)
  begin
    set local session_replication_role = replica;
  exception when others then null;
  end;
  if src is null then  -- the league whose active season is the Legacy Dynasty Sleeper league
    select s.league_id into src from public.league_seasons s
    where s.is_active and s.sleeper_league_id = '1361083066197491712' limit 1;
  end if;
  if src is null or coalesce((select is_test from public.leagues where id = src), false) then
    raise exception 'source league not found';
  end if;

  drop table if exists _test_map;
  drop table if exists _test_names;
  create temp table _test_map(old text primary key, new text not null) on commit drop;
  create temp table _test_names(old text primary key, new text not null) on commit drop;
  insert into _test_map values (src::text, dst::text);

  -- every public table with a league_id column, minus the skip list
  select array_agg(c.table_name::text order by c.table_name) into tables
  from information_schema.columns c
  join information_schema.tables tb on tb.table_schema = c.table_schema and tb.table_name = c.table_name and tb.table_type = 'BASE TABLE'
  where c.table_schema = 'public' and c.column_name = 'league_id' and not public.test_league_skip_private(c.table_name::text);

  -- new ids for every copied row (and the commissioner's membership), before anything is written: each table's
  -- uuid primary key, whatever it's called (id, stable_pick_id, ...). Older tables keep league_id as text.
  foreach t in array tables || array['league_memberships'] loop
    for cols in
      select a.attname::text from pg_index i
      join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
      where i.indrelid = format('public.%I', t)::regclass and i.indisprimary and i.indnatts = 1
        and a.atttypid = 'uuid'::regtype and a.attname <> 'league_id'
    loop
      execute format('insert into _test_map select %1$I::text, gen_random_uuid()::text from public.%2$I where league_id::text = $1::text and %1$I is not null on conflict do nothing', cols, t) using src;
    end loop;
  end loop;

  -- fake names, by roster number
  insert into _test_names
  select owner_name, 'Owner ' || sleeper_roster_id from public.league_teams
  where league_id = src and nullif(btrim(owner_name), '') is not null and sleeper_roster_id is not null
  on conflict do nothing;
  insert into _test_names
  select team_name, 'Test Team ' || sleeper_roster_id from public.league_teams
  where league_id = src and nullif(btrim(team_name), '') is not null and sleeper_roster_id is not null
  on conflict do nothing;

  -- the league row itself
  select string_agg(quote_ident(column_name), ',' order by ordinal_position) into cols
  from information_schema.columns where table_schema = 'public' and table_name = 'leagues'
    and is_generated = 'NEVER' and coalesce(identity_generation, '') <> 'ALWAYS';
  execute format('insert into public.leagues (%1$s) select %1$s from (select (jsonb_populate_record(null::public.leagues,
                    public.test_league_remap_private(to_jsonb(l)) || jsonb_build_object(''name'', ''Legacy Test'', ''is_test'', true, ''sleeper_league_id'', null))).*
                  from public.leagues l where l.id = $1) r', cols) using src;

  -- copy table by table; one that fails (it needs a row from a table not copied yet) is tried again on the next pass
  pending := tables || array['league_memberships'];
  loop
    progress := false;
    errs := '{}';
    foreach t in array pending loop
      select string_agg(quote_ident(column_name), ',' order by ordinal_position) into cols
      from information_schema.columns where table_schema = 'public' and table_name = t
        and is_generated = 'NEVER' and coalesce(identity_generation, '') <> 'ALWAYS'
        and not (column_name = 'id' and data_type <> 'uuid' and column_default is not null);  -- serial ids: new numbers
      -- keys unique across the whole database get a test suffix; Sleeper league ids become the mirror id
      select coalesce(' || jsonb_strip_nulls(jsonb_build_object(' || string_agg(
               case when column_name = 'sleeper_league_id'
                    then format('%L, case when x.%I ~ ''^[0-9]+$'' then ''mirror-'' || translate(x.%I, ''0123456789'', ''abcdefghij'') else x.%I end', column_name, column_name, column_name, column_name)
                    else format('%L, x.%I || %L', column_name, column_name, tag) end, ', ') || '))', '') into extra
      from information_schema.columns where table_schema = 'public' and table_name = t and data_type = 'text'
        and (column_name ~ '(_key|_token)$' or column_name = 'sleeper_league_id');
      begin
        execute format('insert into public.%1$I (%2$s) select %2$s from (select (jsonb_populate_record(null::public.%1$I,
                          public.test_league_remap_private(to_jsonb(x))%4$s)).* from public.%1$I x where x.league_id::text = $1::text %3$s) r',
                       t, cols, case when t = 'league_memberships' then 'and x.role = ''commissioner''' else '' end, extra) using src;
        get diagnostics n = row_count;
        copied := copied || jsonb_build_object(t, n);
        pending := array_remove(pending, t);
        progress := true;
      exception when others then
        errs := errs || jsonb_build_object(t, sqlerrm);
      end;
    end loop;
    exit when pending = '{}' or not progress;
  end loop;
  failed := errs;
  if coalesce(array_length(pending, 1), 0) > 0 then
    -- leave nothing half made: a failed table means the copy isn't usable
    raise exception 'test league copy failed: %', failed::text;
  end if;

  -- the app keeps opening the real league until the commissioner picks the test one in Settings > Leagues
  insert into public.user_active_league(user_id, league_id)
  select distinct user_id, src from public.league_memberships where league_id = dst and user_id is not null
  on conflict (user_id) do nothing;

  -- this week, the same way the trade deadline check counts it
  select min(g.week) into this_week from public.nfl_games g
  where g.season = (select max(season) from public.nfl_games) and g.week <= 18
    and g.gameday >= (date_trunc('day', now() at time zone 'America/New_York')
        - make_interval(days => ((extract(isodow from now() at time zone 'America/New_York')::int - 2 + 7) % 7)))::date;
  insert into public.league_app_transactions as a (league_id, enabled, own_from_week, updated_at)
  values (dst, false, coalesce(this_week, 1), clock_timestamp())
  on conflict (league_id) do update set enabled = false, own_from_week = excluded.own_from_week, updated_at = clock_timestamp();

  return jsonb_build_object('test_league_id', dst, 'own_from_week', this_week, 'copied', copied);
end $$;
revoke all on function public.create_test_league(uuid) from public, anon, authenticated;
revoke all on function public.test_league_remap_private(jsonb) from public, anon, authenticated;

-- Removes a test league and everything in it (never a real league).
create or replace function public.delete_test_league(p_league_id uuid) returns jsonb
language plpgsql set search_path = pg_catalog, public as $$
declare
  tables text[];
  pending text[];
  t text;
  n int;
  progress boolean;
  removed jsonb := '{}';
  errs jsonb;
  replica boolean := true;
begin
  if not coalesce((select is_test from public.leagues where id = p_league_id), false) then raise exception 'not_a_test_league'; end if;
  -- the contract guards refuse deletes; they're skipped for this (only rows of the test league are touched)
  begin
    set local session_replication_role = replica;
  exception when others then replica := false;
  end;
  select array_agg(c.table_name::text) into tables
  from information_schema.columns c
  join information_schema.tables tb on tb.table_schema = c.table_schema and tb.table_name = c.table_name and tb.table_type = 'BASE TABLE'
  where c.table_schema = 'public' and c.column_name = 'league_id' and c.table_name <> 'leagues';
  pending := tables;
  loop
    progress := false;
    errs := '{}';
    foreach t in array pending loop
      begin
        if not replica then execute format('alter table public.%I disable trigger user', t); end if;
        execute format('delete from public.%I where league_id::text = $1::text', t) using p_league_id;
        get diagnostics n = row_count;
        if not replica then execute format('alter table public.%I enable trigger user', t); end if;
        if n > 0 then removed := removed || jsonb_build_object(t, n); end if;
        pending := array_remove(pending, t);
        progress := true;
      exception when others then
        errs := errs || jsonb_build_object(t, sqlerrm);
      end;
    end loop;
    exit when pending = '{}' or not progress;
  end loop;
  if coalesce(array_length(pending, 1), 0) > 0 then raise exception 'test league delete failed: %', errs::text; end if;
  delete from public.leagues where id = p_league_id;
  return removed;
end $$;
revoke all on function public.delete_test_league(uuid) from public, anon, authenticated;

commit;
