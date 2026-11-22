-- ============================================================================
-- 0001 — extensions, functions, profiles, workspaces, membership
-- ============================================================================
-- Workspace-based multi-tenancy from day one. Every piece of knowledge belongs
-- to a workspace, never directly to a user. A user reaches data only through
-- membership (docs/DATABASE.md §2).
--
-- This is the migration that makes the SaaS path a membership change rather
-- than a redesign.
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
  id                     uuid primary key references auth.users(id) on delete cascade,
  display_name           text,
  avatar_url             text,
  timezone               text not null default 'UTC',
  -- filled in by handle_new_user() below; the FK is added after workspaces
  -- exists, because profiles is intentionally the first table in this file
  default_workspace_id   uuid,
  default_embedding_model text not null default 'text-embedding-3-small',
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

drop trigger if exists trg_profiles_updated_at on public.profiles;
create trigger trg_profiles_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- workspaces
-- ---------------------------------------------------------------------------
create table if not exists public.workspaces (
  id         uuid primary key default gen_random_uuid(),
  name       text not null check (char_length(btrim(name)) between 1 and 120),
  slug       text not null check (slug ~ '^[a-z0-9][a-z0-9-]{1,60}[a-z0-9]$'),
  owner_id   uuid not null references auth.users(id) on delete cascade,
  -- true for the workspace auto-created at signup; false once a user makes one
  personal   boolean not null default true,
  metadata   jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_workspaces_slug on public.workspaces (slug);
create index if not exists idx_workspaces_owner on public.workspaces (owner_id);

drop trigger if exists trg_workspaces_updated_at on public.workspaces;
create trigger trg_workspaces_updated_at
  before update on public.workspaces
  for each row execute function public.set_updated_at();

-- Deferred from the profiles definition above (workspaces did not exist yet).
-- SET NULL rather than CASCADE: losing a workspace must not delete the user.
alter table public.profiles
  drop constraint if exists profiles_default_workspace_id_fkey,
  add constraint profiles_default_workspace_id_fkey
    foreign key (default_workspace_id) references public.workspaces(id)
    on delete set null;

-- ---------------------------------------------------------------------------
-- workspace_members — the single source of tenancy truth
-- ---------------------------------------------------------------------------
create table if not exists public.workspace_members (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id      uuid not null references auth.users(id) on delete cascade,
  role         text not null default 'member'
                 check (role in ('owner', 'admin', 'member', 'viewer')),
  invited_by   uuid references auth.users(id) on delete set null,
  joined_at    timestamptz not null default now(),
  created_at   timestamptz not null default now(),
  primary key (workspace_id, user_id)
);

create index if not exists idx_workspace_members_user on public.workspace_members (user_id);

-- Exactly one owner row per workspace, enforced in the database rather than by
-- convention. Transferring ownership is an explicit two-statement swap.
create unique index if not exists uq_workspace_single_owner
  on public.workspace_members (workspace_id)
  where role = 'owner';

-- ============================================================================
-- Membership helpers
-- ----------------------------------------------------------------------------
-- These are SECURITY DEFINER on purpose. RLS policies on every knowledge table
-- call them; if they were SECURITY INVOKER, each policy check would itself be
-- subject to RLS on workspace_members and recurse. `search_path` is pinned so a
-- caller cannot shadow `public` objects.
--
-- Roles:
--   owner / admin  → manage workspace, invite, delete anything
--   member         → read + write content
--   viewer         → read only (the row that makes sharing safe to add later)
-- ============================================================================

create or replace function public.workspace_role(p_workspace_id uuid)
returns text
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select wm.role
  from public.workspace_members wm
  where wm.workspace_id = p_workspace_id
    and wm.user_id = auth.uid()
$$;

create or replace function public.is_workspace_member(p_workspace_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1 from public.workspace_members wm
    where wm.workspace_id = p_workspace_id
      and wm.user_id = auth.uid()
  )
$$;

create or replace function public.can_write_workspace(p_workspace_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce(
    (select wm.role in ('owner', 'admin', 'member')
     from public.workspace_members wm
     where wm.workspace_id = p_workspace_id
       and wm.user_id = auth.uid()),
    false
  )
$$;

create or replace function public.can_manage_workspace(p_workspace_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce(
    (select wm.role in ('owner', 'admin')
     from public.workspace_members wm
     where wm.workspace_id = p_workspace_id
       and wm.user_id = auth.uid()),
    false
  )
$$;

-- Used by the profiles policy so members of a shared workspace can render each
-- other's display names without exposing every profile in the database.
create or replace function public.shares_workspace(p_user_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.workspace_members mine
    join public.workspace_members theirs
      on theirs.workspace_id = mine.workspace_id
    where mine.user_id = auth.uid()
      and theirs.user_id = p_user_id
  )
$$;

-- ---------------------------------------------------------------------------
-- Signup: create profile, personal workspace, and owner membership atomically.
-- SECURITY DEFINER because auth.users is not writable by user-facing roles.
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_display text;
  v_slug    text;
  v_ws_id   uuid;
begin
  v_display := coalesce(
    nullif(new.raw_user_meta_data ->> 'full_name', ''),
    split_part(new.email, '@', 1)
  );

  insert into public.profiles (id, display_name)
  values (new.id, v_display)
  on conflict (id) do nothing;

  -- Slug: readable base plus a short suffix so two users called "alex" cannot
  -- collide on signup.
  v_slug := regexp_replace(lower(coalesce(v_display, 'workspace')), '[^a-z0-9]+', '-', 'g');
  v_slug := btrim(v_slug, '-');
  if char_length(v_slug) < 2 then
    v_slug := 'workspace';
  end if;
  v_slug := left(v_slug, 40) || '-' || substr(replace(new.id::text, '-', ''), 1, 6);

  insert into public.workspaces (name, slug, owner_id, personal)
  values (coalesce(v_display, 'My workspace') || '''s workspace', v_slug, new.id, true)
  returning id into v_ws_id;

  insert into public.workspace_members (workspace_id, user_id, role)
  values (v_ws_id, new.id, 'owner')
  on conflict (workspace_id, user_id) do nothing;

  -- Remember where to land on first load. A user with no default workspace
  -- would have to guess, and "the workspace you were created with" is always
  -- the right answer at signup.
  update public.profiles set default_workspace_id = v_ws_id where id = new.id;

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
