-- Owner activity: every screen, tap, swipe, action and session replay picture, in our own database.
-- Only accounts listed in app_activity_viewers can read it (the league owner's account; added by hand, see the end).

-- 1. More kinds of rows in the activity table (old app builds keep sending plain screen views).
alter table public.app_screen_views add column if not exists kind text not null default 'screen';
alter table public.app_screen_views add column if not exists detail jsonb;
alter table public.app_screen_views drop constraint if exists app_screen_views_kind_check;
alter table public.app_screen_views add constraint app_screen_views_kind_check
  check (kind in ('screen', 'action', 'tap', 'swipe', 'push_open', 'app_open', 'app_close', 'error', 'frame',
                  'load', 'rage', 'search', 'outbound', 'refresh', 'feedback', 'fail'));
alter table public.app_screen_views drop constraint if exists app_screen_views_detail_size;
alter table public.app_screen_views add constraint app_screen_views_detail_size check (detail is null or octet_length(detail::text) <= 1000);
create index if not exists app_screen_views_league_user_time_idx on public.app_screen_views (league_id, user_id, viewed_at);
grant select, insert on public.app_screen_views to authenticated;
grant usage, select on sequence public.app_screen_views_id_seq to authenticated;

-- 2. Who may see it.
create table if not exists public.app_activity_viewers (
  user_id uuid primary key,
  added_at timestamptz not null default now()
);
alter table public.app_activity_viewers enable row level security;
drop policy if exists app_activity_viewers_self on public.app_activity_viewers;
create policy app_activity_viewers_self on public.app_activity_viewers for select to authenticated using (user_id = auth.uid());
grant select on public.app_activity_viewers to authenticated;

drop policy if exists app_screen_views_viewer_read on public.app_screen_views;
create policy app_screen_views_viewer_read on public.app_screen_views for select to authenticated
  using (exists (select 1 from public.app_activity_viewers v where v.user_id = auth.uid())
         and exists (select 1 from public.league_memberships m where m.league_id = app_screen_views.league_id and m.user_id = auth.uid()));

drop policy if exists league_ai_usage_viewer_read on public.league_ai_usage;
create policy league_ai_usage_viewer_read on public.league_ai_usage for select to authenticated
  using (exists (select 1 from public.app_activity_viewers v where v.user_id = auth.uid())
         and exists (select 1 from public.league_memberships m where m.league_id = league_ai_usage.league_id and m.user_id = auth.uid()));

-- 3. Session replay pictures: a private bucket. Each owner uploads only into their own folder; only viewers can look.
insert into storage.buckets (id, name, public) values ('replays', 'replays', false) on conflict (id) do nothing;
drop policy if exists replays_owner_upload on storage.objects;
create policy replays_owner_upload on storage.objects for insert to authenticated
  with check (bucket_id = 'replays' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists replays_viewer_read on storage.objects;
create policy replays_viewer_read on storage.objects for select to authenticated
  using (bucket_id = 'replays' and exists (select 1 from public.app_activity_viewers v where v.user_id = auth.uid()));

-- 5. Saved console reports: each account sees and changes only its own.
create table if not exists public.activity_reports (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid(),
  spec jsonb not null,
  created_at timestamptz not null default now()
);
alter table public.activity_reports enable row level security;
drop policy if exists activity_reports_own on public.activity_reports;
create policy activity_reports_own on public.activity_reports for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
grant select, insert, update, delete on public.activity_reports to authenticated;

-- 6. Owner feedback (Settings > Send feedback, and the weekly check-in): owners add their own; only viewers read.
create table if not exists public.app_feedback (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid(),
  league_id uuid references public.leagues(id) on delete cascade,
  kind text not null default 'button' check (kind in ('button', 'pulse')),
  rating int check (rating between 1 and 5),
  text text check (text is null or length(text) <= 2000),
  screen text,
  created_at timestamptz not null default now()
);
alter table public.app_feedback enable row level security;
drop policy if exists app_feedback_own_insert on public.app_feedback;
create policy app_feedback_own_insert on public.app_feedback for insert to authenticated with check (user_id = auth.uid());
drop policy if exists app_feedback_viewer_read on public.app_feedback;
create policy app_feedback_viewer_read on public.app_feedback for select to authenticated
  using (exists (select 1 from public.app_activity_viewers v where v.user_id = auth.uid()));
grant select, insert on public.app_feedback to authenticated;

-- 7. Alerts sent, so Owner activity can show which alerts get opened (viewers only).
drop policy if exists push_outbox_viewer_read on public.push_outbox;
create policy push_outbox_viewer_read on public.push_outbox for select to authenticated
  using (exists (select 1 from public.app_activity_viewers v where v.user_id = auth.uid())
         and exists (select 1 from public.league_memberships m where m.league_id = push_outbox.league_id and m.user_id = auth.uid()));

-- 4. Add the viewer (run once, with the account that should see Activity):
-- insert into public.app_activity_viewers (user_id) select id from auth.users where email = 'YOUR SIGN-IN EMAIL' on conflict do nothing;
-- Let every owner's phone finish saving its own replay pictures.
-- Saving a picture "over" an existing one needs the owner to be able to see and update their OWN folder.
-- Nobody can see anyone else's pictures; only your account still sees everyone's (in Owner activity).
drop policy if exists replays_owner_own_read on storage.objects;
create policy replays_owner_own_read on storage.objects for select to authenticated
  using (bucket_id = 'replays' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists replays_owner_own_update on storage.objects;
create policy replays_owner_own_update on storage.objects for update to authenticated
  using (bucket_id = 'replays' and (storage.foldername(name))[1] = auth.uid()::text);
