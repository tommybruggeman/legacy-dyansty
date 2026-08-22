-- Stage 2: owner invitation schema and shared acceptance RPC.
-- This migration is intentionally limited to invitation-related support.
-- It preserves legacy league_memberships.team_id and league_invites.team_id.

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

alter table public.league_memberships
    add column if not exists league_team_id uuid;

alter table public.league_invites
    add column if not exists league_team_id uuid,
    add column if not exists token_hash text,
    add column if not exists accepted_at timestamptz,
    add column if not exists accepted_by_user_id uuid,
    add column if not exists revoked_at timestamptz,
    add column if not exists last_sent_at timestamptz,
    add column if not exists send_count integer not null default 0;

alter table public.league_invites
    alter column token drop not null;

update public.league_invites
set email = lower(trim(email))
where email is not null
  and email <> lower(trim(email));

-- Revoke legacy pending rows that cannot safely participate in the new flow.
update public.league_invites li
set status = 'revoked',
    revoked_at = coalesce(li.revoked_at, now())
where li.status = 'pending'
  and (
      li.expires_at < now()
      or not exists (
          select 1
          from public.leagues l
          where l.id = li.league_id
      )
      or li.league_team_id is null
      or not exists (
          select 1
          from public.league_teams lt
          where lt.id = li.league_team_id
            and lt.league_id = li.league_id
      )
  );

-- Preserve the newest valid pending invite in each group and revoke duplicates.
with ranked_pending as (
    select
        id,
        row_number() over (
            partition by league_id, league_team_id, lower(email)
            order by
                case when expires_at is null or expires_at >= now() then 0 else 1 end,
                created_at desc,
                id desc
        ) as rn
    from public.league_invites
    where status = 'pending'
      and league_team_id is not null
)
update public.league_invites li
set status = 'revoked',
    revoked_at = coalesce(li.revoked_at, now())
from ranked_pending rp
where li.id = rp.id
  and rp.rn > 1;

do $$
begin
    if exists (
        select 1
        from public.league_invites li
        where li.league_id is not null
          and not exists (
              select 1
              from public.leagues l
              where l.id = li.league_id
          )
    ) then
        raise exception 'Cannot add league_invites.league_id foreign key: orphaned non-pending invite rows exist.';
    end if;

    if exists (
        select 1
        from public.league_invites li
        where li.league_team_id is not null
          and not exists (
              select 1
              from public.league_teams lt
              where lt.id = li.league_team_id
          )
    ) then
        raise exception 'Cannot add league_invites.league_team_id foreign key: invalid league_team_id values exist.';
    end if;

    -- invited_by is metadata about who sent the invite; nulling invalid legacy
    -- references preserves the invitation row and allows the FK to be added.
    update public.league_invites li
    set invited_by = null
    where li.invited_by is not null
      and not exists (
          select 1
          from auth.users u
          where u.id = li.invited_by
      );

    if exists (
        select 1
        from public.league_invites li
        where li.accepted_by_user_id is not null
          and not exists (
              select 1
              from auth.users u
              where u.id = li.accepted_by_user_id
          )
    ) then
        raise exception 'Cannot add league_invites.accepted_by_user_id foreign key: invalid accepted_by_user_id values exist.';
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'league_memberships_league_team_id_fkey'
          and conrelid = 'public.league_memberships'::regclass
    ) then
        alter table public.league_memberships
            add constraint league_memberships_league_team_id_fkey
            foreign key (league_team_id)
            references public.league_teams(id)
            on delete set null;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'league_invites_league_id_fkey'
          and conrelid = 'public.league_invites'::regclass
    ) then
        alter table public.league_invites
            add constraint league_invites_league_id_fkey
            foreign key (league_id)
            references public.leagues(id)
            on delete cascade;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'league_invites_league_team_id_fkey'
          and conrelid = 'public.league_invites'::regclass
    ) then
        alter table public.league_invites
            add constraint league_invites_league_team_id_fkey
            foreign key (league_team_id)
            references public.league_teams(id)
            on delete set null;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'league_invites_invited_by_fkey'
          and conrelid = 'public.league_invites'::regclass
    ) then
        alter table public.league_invites
            add constraint league_invites_invited_by_fkey
            foreign key (invited_by)
            references auth.users(id)
            on delete set null;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'league_invites_accepted_by_user_id_fkey'
          and conrelid = 'public.league_invites'::regclass
    ) then
        alter table public.league_invites
            add constraint league_invites_accepted_by_user_id_fkey
            foreign key (accepted_by_user_id)
            references auth.users(id)
            on delete set null;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'league_invites_send_count_nonnegative'
          and conrelid = 'public.league_invites'::regclass
    ) then
        alter table public.league_invites
            add constraint league_invites_send_count_nonnegative
            check (send_count >= 0);
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'league_invites_email_normalized'
          and conrelid = 'public.league_invites'::regclass
    ) then
        alter table public.league_invites
            add constraint league_invites_email_normalized
            check (email = lower(trim(email)));
    end if;

end
$$;

create index if not exists league_memberships_league_team_id_idx
    on public.league_memberships(league_team_id);

create index if not exists league_invites_league_id_idx
    on public.league_invites(league_id);

create index if not exists league_invites_league_team_id_idx
    on public.league_invites(league_team_id);

create index if not exists league_invites_status_idx
    on public.league_invites(status);

create index if not exists league_invites_normalized_email_idx
    on public.league_invites(lower(email));

create unique index if not exists league_invites_token_hash_unique_idx
    on public.league_invites(token_hash)
    where token_hash is not null;

create index if not exists league_invites_pending_lookup_idx
    on public.league_invites(token_hash, status, expires_at)
    where status = 'pending';

-- PostgreSQL does not allow now() in a partial unique index predicate because
-- index predicates must be immutable. Keep one pending invite per team/email;
-- application code revokes expired pending rows before inserting a replacement.
create unique index if not exists league_invites_one_pending_team_email_idx
    on public.league_invites(league_id, league_team_id, lower(email))
    where status = 'pending'
      and league_team_id is not null;

alter table public.league_invites enable row level security;

do $$
declare
    policy record;
begin
    for policy in
        select policyname
        from pg_policies
        where schemaname = 'public'
          and tablename = 'league_invites'
          and cmd = 'INSERT'
        and coalesce(with_check, '') in ('true', '(true)')
    loop
        execute format('drop policy if exists %I on public.league_invites', policy.policyname);
    end loop;
end
$$;

drop policy if exists league_invites_commissioner_select on public.league_invites;
drop policy if exists league_invites_commissioner_insert on public.league_invites;
drop policy if exists league_invites_commissioner_update on public.league_invites;

create policy league_invites_commissioner_select
on public.league_invites
for select
to authenticated
using (
    exists (
        select 1
        from public.league_memberships lm
        where lm.league_id = league_invites.league_id
          and lm.user_id = auth.uid()
          and lm.role = 'commissioner'
    )
);

create policy league_invites_commissioner_insert
on public.league_invites
for insert
to authenticated
with check (
    invited_by = auth.uid()
    and email = lower(trim(email))
    and token is null
    and token_hash is not null
    and league_team_id is not null
    and status = 'pending'
    and exists (
        select 1
        from public.league_memberships lm
        where lm.league_id = league_invites.league_id
          and lm.user_id = auth.uid()
          and lm.role = 'commissioner'
    )
    and exists (
        select 1
        from public.league_teams lt
        where lt.id = league_invites.league_team_id
          and lt.league_id = league_invites.league_id
    )
);

create policy league_invites_commissioner_update
on public.league_invites
for update
to authenticated
using (
    exists (
        select 1
        from public.league_memberships lm
        where lm.league_id = league_invites.league_id
          and lm.user_id = auth.uid()
          and lm.role = 'commissioner'
    )
)
with check (
    exists (
        select 1
        from public.league_memberships lm
        where lm.league_id = league_invites.league_id
          and lm.user_id = auth.uid()
          and lm.role = 'commissioner'
    )
);

create or replace function public.accept_league_invite(raw_token text)
returns table (
    league_id uuid,
    league_team_id uuid,
    membership_id uuid,
    role text,
    team_name text
)
language plpgsql
security definer
set search_path = pg_catalog, extensions
as $$
declare
    invite_row public.league_invites%rowtype;
    auth_email text;
    hashed_token text;
    saved_membership public.league_memberships%rowtype;
    team_row public.league_teams%rowtype;
begin
    if auth.uid() is null then
        raise exception 'Authentication required';
    end if;

    if raw_token is null or length(trim(raw_token)) = 0 then
        raise exception 'Invitation token is required';
    end if;

    select lower(trim(email))
    into auth_email
    from auth.users
    where id = auth.uid();

    if auth_email is null then
        raise exception 'Authenticated user email not found';
    end if;

    hashed_token := pg_catalog.encode(extensions.digest(raw_token, 'sha256'), 'hex');

    select *
    into invite_row
    from public.league_invites
    where token_hash = hashed_token
    limit 1
    for update;

    if not found then
        raise exception 'Invitation not found';
    end if;

    if invite_row.status <> 'pending' then
        raise exception 'Invitation is not pending';
    end if;

    if invite_row.expires_at is not null and invite_row.expires_at < now() then
        raise exception 'Invitation has expired';
    end if;

    if lower(trim(invite_row.email)) <> auth_email then
        raise exception 'Invitation email does not match authenticated user';
    end if;

    if invite_row.league_team_id is null then
        raise exception 'Invitation is missing an assigned team';
    end if;

    select *
    into team_row
    from public.league_teams
    where id = invite_row.league_team_id
      and league_id = invite_row.league_id;

    if not found then
        raise exception 'Invitation team is invalid';
    end if;

    insert into public.league_memberships (
        league_id,
        user_id,
        role,
        league_team_id
    )
    values (
        invite_row.league_id,
        auth.uid(),
        'member',
        invite_row.league_team_id
    )
    on conflict (league_id, user_id) do update
    set league_team_id = excluded.league_team_id,
        role = case
            when public.league_memberships.role = 'commissioner' then public.league_memberships.role
            else 'member'
        end
    returning * into saved_membership;

    update public.league_invites
    set status = 'accepted',
        accepted_at = now(),
        accepted_by_user_id = auth.uid()
    where id = invite_row.id;

    league_id := invite_row.league_id;
    league_team_id := invite_row.league_team_id;
    membership_id := saved_membership.id;
    role := saved_membership.role;
    team_name := coalesce(team_row.team_name, team_row.owner_name);
    return next;
end;
$$;

revoke all on function public.accept_league_invite(text) from public;
grant execute on function public.accept_league_invite(text) to authenticated;

comment on column public.league_memberships.team_id is
    'Legacy relationship to owners.id. Do not repurpose for league_teams access.';

comment on column public.league_memberships.league_team_id is
    'Canonical invitation/team access relationship to league_teams.id.';

comment on column public.league_invites.team_id is
    'Legacy invitation team field. New invitations must use league_team_id.';

comment on column public.league_invites.token_hash is
    'SHA-256 hash of the raw invitation token. New invitations must not store raw tokens.';

comment on table public.league_teams is
    'RLS is intentionally not changed in Stage 2. Existing disabled RLS remains a known risk to review separately.';
