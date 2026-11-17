-- App Store readiness: delete-my-account, "report this answer" for the Front Office GM, and screen-time tracking.
-- Safe to run more than once.

-- ---------------------------------------------------------------------------------------------
-- 1. Owners flag a GM answer that's wrong. Read by the weekly AI report and the learning job.
-- ---------------------------------------------------------------------------------------------
create table if not exists public.league_ai_feedback (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  league_id uuid references public.leagues(id) on delete cascade,
  user_id uuid not null default auth.uid(),
  conversation_id uuid,
  question text check (question is null or char_length(question) <= 2000),
  answer text check (answer is null or char_length(answer) <= 8000),
  reason text not null check (reason in ('wrong', 'outdated', 'harmful', 'other')),
  note text check (note is null or char_length(note) <= 500),
  source text not null default 'app' check (source in ('app', 'website'))
);
create index if not exists league_ai_feedback_league_time_idx on public.league_ai_feedback (league_id, created_at desc);

alter table public.league_ai_feedback enable row level security;
drop policy if exists league_ai_feedback_insert_own on public.league_ai_feedback;
create policy league_ai_feedback_insert_own on public.league_ai_feedback for insert to authenticated
  with check (user_id = auth.uid());
drop policy if exists league_ai_feedback_select_own on public.league_ai_feedback;
create policy league_ai_feedback_select_own on public.league_ai_feedback for select to authenticated
  using (user_id = auth.uid());

-- ---------------------------------------------------------------------------------------------
-- 2. Delete my account (Apple requires this inside the app).
--    Removes the owner's personal data and sign-in. League records (contracts, trades, lineups)
--    stay in the league history, as the privacy policy says. If a league record still points at
--    the sign-in, the sign-in is scrubbed and locked instead of removed.
-- ---------------------------------------------------------------------------------------------
create or replace function public.delete_my_account()
returns jsonb
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  uid uuid := auth.uid();
  t text;
  removed boolean := false;
begin
  if uid is null then
    raise exception 'Sign in first.';
  end if;
  if exists (select 1 from auth.users where id = uid and lower(email) = 'applereview@legacydynasty.app') then
    raise exception 'This is a demo account, so it can''t be deleted.';
  end if;
  if exists (select 1 from public.league_memberships where user_id = uid and role = 'commissioner') then
    raise exception 'You''re the commissioner. Another owner has to take over as commissioner first; email support for help.';
  end if;

  -- Personal app data.
  foreach t in array array[
    'league_ai_conversations', 'league_ai_briefs', 'league_ai_memory', 'league_ai_feedback',
    'market_targets', 'owner_gm_prefs', 'push_tokens', 'push_prefs', 'push_outbox', 'live_activities', 'app_screen_views',
    'league_memberships'
  ] loop
    if to_regclass('public.' || t) is not null then
      execute format('delete from public.%I where user_id = $1', t) using uid;
    end if;
  end loop;
  if to_regclass('public.league_invites') is not null then
    update public.league_invites set status = 'revoked'
      where status = 'pending' and lower(email) = (select lower(email) from auth.users where id = uid);
  end if;
  -- Usage logs keep their cost numbers but lose the link to the person.
  if to_regclass('public.league_ai_usage') is not null then
    update public.league_ai_usage set user_id = null where user_id = uid;
  end if;

  -- The sign-in itself.
  begin
    delete from auth.users where id = uid;
    removed := true;
  exception when foreign_key_violation then
    update auth.users
       set email = 'deleted-' || uid || '@deleted.invalid',
           phone = null,
           encrypted_password = '',
           raw_user_meta_data = '{}'::jsonb,
           raw_app_meta_data = '{"deleted": true}'::jsonb,
           banned_until = 'infinity'
     where id = uid;
    delete from auth.identities where user_id = uid;
    delete from auth.sessions where user_id = uid;
    delete from auth.refresh_tokens where user_id = uid::text;
  end;
  return jsonb_build_object('ok', true, 'removed', removed);
end;
$$;

revoke all on function public.delete_my_account() from public, anon;
grant execute on function public.delete_my_account() to authenticated;

-- ---------------------------------------------------------------------------------------------
-- 3. Screen time: which pages owners open and how long they stay (our own analytics, no outside service).
-- ---------------------------------------------------------------------------------------------
create table if not exists public.app_screen_views (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  viewed_at timestamptz not null default now(),
  user_id uuid not null default auth.uid(),
  league_id uuid references public.leagues(id) on delete cascade,
  screen text not null check (char_length(screen) <= 120),
  seconds integer not null default 0 check (seconds between 0 and 1800),
  build text,
  source text not null default 'app' check (source in ('app', 'website'))
);
create index if not exists app_screen_views_league_time_idx on public.app_screen_views (league_id, viewed_at desc);

alter table public.app_screen_views enable row level security;
drop policy if exists app_screen_views_insert_own on public.app_screen_views;
create policy app_screen_views_insert_own on public.app_screen_views for insert to authenticated
  with check (user_id = auth.uid());
