-- PostgreSQL-only compatibility objects for applying production_schema.sql
-- outside Supabase. This file contains no application schema or production data.
do $$begin
 if not exists(select 1 from pg_roles where rolname='postgres') then create role postgres nologin superuser;end if;
 if not exists(select 1 from pg_roles where rolname='anon') then create role anon nologin;end if;
 if not exists(select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin;end if;
 if not exists(select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls;end if;
end$$;

create schema if not exists extensions;
create schema if not exists vault;
create schema if not exists auth;
create publication supabase_realtime;

create table if not exists auth.users(
 id uuid primary key,
 email text,
 created_at timestamptz not null default now()
);

create or replace function auth.uid() returns uuid language sql stable as $$
 select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid
$$;

create or replace function auth.role() returns text language sql stable as $$
 select coalesce(nullif(current_setting('request.jwt.claim.role',true),''),current_user)
$$;
