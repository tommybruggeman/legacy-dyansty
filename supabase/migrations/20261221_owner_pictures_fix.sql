-- Fix "new row violates row-level security policy" when an owner uploads a team picture.
create or replace function public.can_edit_owner_picture(folder text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.league_memberships lm
    join public.league_teams lt on lt.league_id = lm.league_id
    where lm.user_id = auth.uid() and lt.id::text = folder
      and (lm.role = 'commissioner' or lm.league_team_id = lt.id));
$$;
grant execute on function public.can_edit_owner_picture(text) to authenticated;

drop policy if exists owner_pictures_write on storage.objects;
create policy owner_pictures_write on storage.objects for insert to authenticated
  with check (bucket_id = 'owner-pictures' and public.can_edit_owner_picture((storage.foldername(name))[1]));

drop policy if exists owner_pictures_update on storage.objects;
create policy owner_pictures_update on storage.objects for update to authenticated
  using (bucket_id = 'owner-pictures' and public.can_edit_owner_picture((storage.foldername(name))[1]));

drop policy if exists owner_pictures_read on storage.objects;
create policy owner_pictures_read on storage.objects for select to authenticated
  using (bucket_id = 'owner-pictures');
