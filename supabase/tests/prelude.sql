-- ============================================================================
-- Supabase-equivalent surface for a bare Postgres
-- ============================================================================
-- The migrations and seed.sql are written against Supabase, which provides
-- things a stock Postgres does not:
--
--   * the `anon` / `authenticated` / `service_role` roles that every grant names;
--   * the `auth` schema with `auth.uid()` and `auth.role()`, which every RLS
--     policy calls;
--   * the `auth.users` table the signup trigger attaches to, and its columns;
--   * the `storage` schema with `storage.objects`, `storage.foldername()` and
--     `storage.filename()`, which migration 0009 writes policies for;
--   * pgcrypto's `crypt()` / `gen_salt()`, used by the seed to hash demo
--     passwords.
--
-- This file creates exactly that surface and nothing else, so migrations and
-- seed scripts run **unmodified** against a plain Postgres.
--
-- Used by:
--   * `.github/workflows/ci.yml` (the `database` job) — the CI gate;
--   * by hand when validating schema changes against a local Postgres.
--
-- It is test scaffolding, not application code, and it must stay faithful to
-- what Supabase actually provides: if this drifts, the tests stop representing
-- production. `supabase start` does not need it — it supplies the real thing.
--
-- pgvector is *not* shimmed here. Real pgvector is required (migration 0003
-- declares `vector(1536)` and builds an HNSW index), so CI uses the
-- `pgvector/pgvector` image and fails loudly on a stock one rather than
-- pretending the schema works.
-- ============================================================================

-- --- roles Supabase creates -------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end
$$;

grant usage on schema public to anon, authenticated, service_role;

-- --- pgcrypto ---------------------------------------------------------------
-- Supabase image: present. pgvector image: absent.
create extension if not exists pgcrypto;

do $do$
begin
  if not exists (select 1 from pg_proc where proname = 'gen_salt') then
    -- Enough of a bcrypt stand-in for seeding. Nothing verifies these values:
    -- authentication is Supabase's job and the API verifies JWTs, not
    -- passwords. Real deployments always have the genuine extension.
    --
    -- The function bodies use $fn$ rather than $$: the enclosing DO block owns
    -- $$, and a matching inner delimiter would end it early with a syntax error
    -- at the first `select`.
    create function gen_salt(kind text default 'bf') returns text
    language sql volatile
    as $fn$ select '$2a$06$' || substr(md5(random()::text), 1, 22) $fn$;

    create function crypt(password text, salt text) returns text
    language sql volatile
    as $fn$ select salt || substr(md5(password || salt), 1, 31) $fn$;
  end if;
end
$do$;

-- --- auth schema ------------------------------------------------------------
create schema if not exists auth;

-- Columns mirror what the seed inserts and what the signup trigger reads.
-- Adding a column here without adding it in Supabase would let a broken seed
-- pass CI, so the shape is deliberately the full set rather than a minimum.
create table if not exists auth.users (
  instance_id            uuid,
  id                     uuid primary key default gen_random_uuid(),
  aud                    text default 'authenticated',
  role                   text default 'authenticated',
  email                  text unique,
  encrypted_password     text,
  email_confirmed_at     timestamptz,
  raw_app_meta_data      jsonb default '{}'::jsonb,
  raw_user_meta_data     jsonb default '{}'::jsonb,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  confirmation_token     text default '',
  recovery_token         text default '',
  email_change_token_new text default '',
  email_change           text default ''
);

-- PostgREST/Supabase expose the JWT claim as a session setting. The test suite
-- sets `request.jwt.claim.sub` to act as a user, which is exactly how the real
-- stack resolves identity inside a policy.
create or replace function auth.uid() returns uuid
language sql stable
as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;

create or replace function auth.role() returns text
language sql stable
as $$
  select coalesce(nullif(current_setting('request.jwt.claim.role', true), ''), 'anon');
$$;

-- --- storage schema ---------------------------------------------------------
create schema if not exists storage;

create table if not exists storage.buckets (
  id                 text primary key,
  name               text not null,
  public             boolean not null default false,
  file_size_limit    bigint,
  allowed_mime_types text[]
);

create table if not exists storage.objects (
  id         uuid primary key default gen_random_uuid(),
  bucket_id  text references storage.buckets(id),
  name       text not null,
  owner      uuid,
  metadata   jsonb,
  created_at timestamptz not null default now()
);

alter table storage.objects enable row level security;

create or replace function storage.foldername(name text) returns text[]
language sql immutable
as $$ select string_to_array(name, '/'); $$;

create or replace function storage.filename(name text) returns text
language sql immutable
as $$
  select (string_to_array(name, '/'))[array_length(string_to_array(name, '/'), 1)];
$$;
