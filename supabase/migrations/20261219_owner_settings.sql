-- Owner Settings in the app (and the website): our own owner names, team names and pictures (no longer Sleeper's),
-- each owner's Front Office preferences, and owners inviting or removing their own co-owner.
begin;
set local search_path = pg_catalog, public;

-- 1. Owner profile: the name, team name and picture the league sees. Empty fields fall back to what we had before.
create table if not exists public.owner_profiles (
  league_team_id uuid primary key references public.league_teams(id) on delete cascade,
  league_id uuid not null,
  display_name text check (display_name is null or char_length(display_name) between 1 and 40),
  team_name text check (team_name is null or char_length(team_name) between 1 and 40),
  team_abbr text check (team_abbr is null or team_abbr ~ '^[A-Z0-9]{2,4}$'),
  picture_url text check (picture_url is null or char_length(picture_url) <= 500),
  updated_by uuid,
  updated_at timestamptz not null default now()
);
create index if not exists owner_profiles_league_idx on public.owner_profiles(league_id);
alter table public.owner_profiles enable row level security;
drop policy if exists owner_profiles_league_read on public.owner_profiles;
create policy owner_profiles_league_read on public.owner_profiles for select to authenticated
  using (exists (select 1 from public.league_memberships lm where lm.league_id = owner_profiles.league_id and lm.user_id = auth.uid()));
grant select on public.owner_profiles to authenticated;

-- Owners (and co-owners) edit their own team's profile; the commissioner can edit any team's.
create or replace function public.save_owner_profile_authenticated(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare tid uuid := (p_request->>'league_team_id')::uuid; lid uuid; actor uuid := auth.uid();
  dn text := nullif(btrim(p_request->>'display_name'), ''); tn text := nullif(btrim(p_request->>'team_name'), '');
  ab text := nullif(upper(btrim(p_request->>'team_abbr')), '');
  pic text := nullif(btrim(p_request->>'picture_url'), ''); keep_pic boolean := not (p_request ? 'picture_url');
begin
  if actor is null then raise exception 'Sign in required'; end if;
  select league_id into lid from public.league_teams where id = tid;
  if lid is null then raise exception 'profile_team_invalid'; end if;
  if not exists (select 1 from public.league_memberships lm where lm.league_id = lid and lm.user_id = actor
                 and (lm.role = 'commissioner' or lm.league_team_id = tid)) then
    raise exception 'profile_not_yours';
  end if;
  if pic is not null and pic !~ '^https://[a-z0-9.-]+\.supabase\.co/storage/v1/object/public/owner-pictures/' then
    raise exception 'profile_picture_invalid';
  end if;
  if ab is not null and ab !~ '^[A-Z0-9]{2,4}$' then raise exception 'profile_abbr_invalid'; end if;
  insert into public.owner_profiles as p (league_team_id, league_id, display_name, team_name, team_abbr, picture_url, updated_by, updated_at)
  values (tid, lid, dn, tn, ab, pic, actor, now())
  on conflict (league_team_id) do update set
    display_name = excluded.display_name, team_name = excluded.team_name, team_abbr = excluded.team_abbr,
    picture_url = case when keep_pic then p.picture_url else excluded.picture_url end,
    updated_by = actor, updated_at = now();
  return (select to_jsonb(p) from public.owner_profiles p where p.league_team_id = tid);
end $$;
revoke all on function public.save_owner_profile_authenticated(jsonb) from public, anon;
grant execute on function public.save_owner_profile_authenticated(jsonb) to authenticated;

-- Pictures: a public bucket, one folder per team. Anyone in the app can see them; only that team's owners
-- (or the commissioner) can upload.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('owner-pictures', 'owner-pictures', true, 1048576, array['image/jpeg', 'image/png'])
on conflict (id) do update set public = true, file_size_limit = 1048576, allowed_mime_types = array['image/jpeg', 'image/png'];

drop policy if exists owner_pictures_write on storage.objects;
create policy owner_pictures_write on storage.objects for insert to authenticated
  with check (bucket_id = 'owner-pictures' and exists (
    select 1 from public.league_memberships lm join public.league_teams lt on lt.league_id = lm.league_id
    where lm.user_id = auth.uid() and lt.id::text = (storage.foldername(name))[1]
      and (lm.role = 'commissioner' or lm.league_team_id = lt.id)));
drop policy if exists owner_pictures_update on storage.objects;
create policy owner_pictures_update on storage.objects for update to authenticated
  using (bucket_id = 'owner-pictures' and exists (
    select 1 from public.league_memberships lm join public.league_teams lt on lt.league_id = lm.league_id
    where lm.user_id = auth.uid() and lt.id::text = (storage.foldername(name))[1]
      and (lm.role = 'commissioner' or lm.league_team_id = lt.id)));

-- 2. Front Office preferences, one row per person.
create table if not exists public.owner_gm_prefs (
  user_id uuid primary key default auth.uid(),
  answer_length text not null default 'short' check (answer_length in ('short', 'detailed')),
  team_plan text not null default 'auto' check (team_plan in ('auto', 'contend', 'rebuild')),
  updated_at timestamptz not null default now()
);
alter table public.owner_gm_prefs enable row level security;
drop policy if exists owner_gm_prefs_own on public.owner_gm_prefs;
create policy owner_gm_prefs_own on public.owner_gm_prefs for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
grant select, insert, update on public.owner_gm_prefs to authenticated;

-- 3. Co-owners: an owner can invite one co-owner to their own team, see and cancel that invite, and remove a co-owner.
drop policy if exists league_invites_owner_select on public.league_invites;
create policy league_invites_owner_select on public.league_invites for select to authenticated
  using (role = 'co_owner' and exists (select 1 from public.league_memberships lm where lm.league_id = league_invites.league_id
         and lm.user_id = auth.uid() and lm.league_team_id = league_invites.league_team_id and lm.role in ('owner', 'commissioner')));
drop policy if exists league_invites_owner_insert on public.league_invites;
create policy league_invites_owner_insert on public.league_invites for insert to authenticated
  with check (role = 'co_owner' and invited_by = auth.uid() and email = lower(trim(email)) and token is null
         and token_hash is not null and status = 'pending'
         and exists (select 1 from public.league_memberships lm where lm.league_id = league_invites.league_id
         and lm.user_id = auth.uid() and lm.league_team_id = league_invites.league_team_id and lm.role in ('owner', 'commissioner')));
drop policy if exists league_invites_owner_update on public.league_invites;
create policy league_invites_owner_update on public.league_invites for update to authenticated
  using (role = 'co_owner' and exists (select 1 from public.league_memberships lm where lm.league_id = league_invites.league_id
         and lm.user_id = auth.uid() and lm.league_team_id = league_invites.league_team_id and lm.role in ('owner', 'commissioner')))
  with check (role = 'co_owner');

-- The people on my team (names only, no emails of other teams).
create or replace function public.my_team_people_authenticated()
returns jsonb language sql security definer stable set search_path = pg_catalog, public as $$
  select coalesce(jsonb_agg(jsonb_build_object('user_id', lm.user_id, 'role', lm.role, 'email', u.email,
                                               'is_me', lm.user_id = auth.uid()) order by lm.role, u.email), '[]'::jsonb)
  from public.league_memberships me
  join public.league_memberships lm on lm.league_id = me.league_id and lm.league_team_id = me.league_team_id
  left join auth.users u on u.id = lm.user_id
  where me.user_id = auth.uid();
$$;
revoke all on function public.my_team_people_authenticated() from public, anon;
grant execute on function public.my_team_people_authenticated() to authenticated;

create or replace function public.remove_co_owner_authenticated(p_user_id uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare me public.league_memberships%rowtype; gone int;
begin
  select * into me from public.league_memberships where user_id = auth.uid() and role in ('owner', 'commissioner') limit 1;
  if me.user_id is null then raise exception 'co_owner_not_allowed'; end if;
  delete from public.league_memberships where user_id = p_user_id and league_id = me.league_id
    and league_team_id = me.league_team_id and lower(role) in ('co_owner', 'co-owner');
  get diagnostics gone = row_count;
  if gone = 0 then raise exception 'co_owner_not_found'; end if;
  return jsonb_build_object('removed', p_user_id);
end $$;
revoke all on function public.remove_co_owner_authenticated(uuid) from public, anon;
grant execute on function public.remove_co_owner_authenticated(uuid) to authenticated;

commit;
