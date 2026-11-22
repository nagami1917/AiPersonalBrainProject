-- ============================================================================
-- 0001 — extensions, helper functions, profiles
-- ============================================================================
-- Everything here is foundational: extensions must exist before any table that
-- uses them, and every later migration depends on public.set_updated_at().
-- ============================================================================

create extension if not exists "vector";
create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

comment on function public.set_updated_at() is
  'Trigger function: stamps updated_at on every UPDATE.';

-- ---------------------------------------------------------------------------
-- profiles — application-level user data, 1:1 with auth.users
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
  id                       uuid primary key references auth.users(id) on delete cascade,
  display_name             text,
  timezone                 text not null default 'UTC',
  default_embedding_model   text not null default 'text-embedding-3-small',
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

comment on table public.profiles is
  'App-level user fields. Row creation is owned by a trigger on auth.users.';

drop trigger if exists trg_profiles_updated_at on public.profiles;
create trigger trg_profiles_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Auto-create a profile when a user signs up.
-- SECURITY DEFINER is required: the auth schema is not writable by the
-- user-facing roles, and this trigger fires inside Supabase's signup path.
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', split_part(new.email, '@', 1)))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
