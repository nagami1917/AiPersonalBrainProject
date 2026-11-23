-- ============================================================================
-- 0019 — an admin cannot become the owner (privilege escalation, P0)
-- ============================================================================
-- ## The defect
--
-- Migration 0007 granted UPDATE on `public.workspace_members` to any holder of
-- `can_manage_workspace()` — owner *or admin* — and 0007's `workspaces_update_manage`
-- did the same for `public.workspaces`. Neither policy restricts **which column**
-- is written. The API route `PATCH /workspaces/{id}/members/{member_id}` does
-- refuse to touch the owner (`cannot_modify_owner`), but the route is not the
-- boundary: the browser holds the anon key and the user's own JWT, so any member
-- can address Supabase PostgREST directly and skip FastAPI entirely.
--
-- Verified against a live database rather than reasoned about from the policy
-- text. Acting as an `admin` in someone else's workspace, with no API involved:
--
--     update workspace_members set role='viewer' where user_id = <the owner>;
--     -- UPDATE 1
--     update workspace_members set role='owner'  where user_id = <me>;
--     -- UPDATE 1
--     update workspaces set owner_id = <me> where id = <the workspace>;
--     -- UPDATE 1
--
-- The unique index `uq_workspace_single_owner` does not stop this; it only
-- forbids *two* owners, so demoting the real owner first frees the slot. Three
-- statements turn an invited admin into the workspace's owner, and with it the
-- audit log, the member roster and every document in the tenant. `DELETE` had
-- the same hole: an admin could delete the owner's membership row outright.
--
-- The point of an owner, as 0015 already states elsewhere, is that ownership is
-- *not* delegated authority — it comes from `workspaces.owner_id` and is not
-- something a manager can grant, least of all to themselves.
--
-- ## The fix, in three layers
--
-- Each layer is a real boundary on its own, because "the route checks it" is
-- exactly the assumption that failed:
--
--   1. **RLS** — UPDATE on `workspace_members` and on `workspaces` now refuses
--      to touch an owner row unless the caller *is* that owner. A policy cannot
--      express "column X may change but column Y may not" (that needs a trigger
--      with OLD and NEW), but it can keep admins away from owner rows, which is
--      what the escalation actually needs.
--   2. **Trigger** — `guard_workspace_ownership()` is the column-level half:
--      no user-facing request may move `workspaces.owner_id`, change an owner's
--      role, or delete an owner's membership. It is deliberately keyed on the
--      *actor* (`auth.uid()`), not on the target: the service role and any
--      `SECURITY DEFINER` function have no request claim and are unaffected, so
--      provisioning and a future ownership-transfer function still work.
--   3. **Privilege** — `authenticated` loses table-level UPDATE on the ownership
--      column outright. Transfers, if they are ever wanted, will go through a
--      `SECURITY DEFINER` function that can be reviewed on its own; until then
--      the browser has no statement it could write that moves ownership.
--
-- Layer 3 is what makes the other two belt-and-braces rather than load-bearing:
-- PostgREST checks table privileges before it consults a policy, so a column the
-- browser cannot update is a column no policy has to defend.
--
-- ## What deliberately still works
--
--   * An admin may still rename *other* people's memberships and remove
--     non-owner members. That is delegated management and it is in 0007's
--     intent; only the owner is fenced off.
--   * A member may still leave (delete their own row) — 0007's
--     `workspace_members_delete_manage_or_self` branch, which is unchanged.
--   * `update public.workspaces set name = ...` (the one workspace write the API
--     performs) is untouched: `name` is still writable by a manager.
--
-- Regression tests: `supabase/tests/ownership_guard.sql` and
-- `apps/api/tests/db/test_owner_protection.py`, both of which fail on 0018.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Column-level privilege: the browser may not write ownership at all.
-- ---------------------------------------------------------------------------
-- Rows are still updatable (name, slug, metadata). Only the column that grants
-- tenancy control is removed.
--
-- Note the shape: a table-level GRANT covers every column, so revoking a single
-- column while the table grant stands is a no-op — Postgres only consults
-- column privileges when there is no table-level privilege. The table grant is
-- therefore revoked and re-issued as an explicit column list. Triggers still
-- write `updated_at` without a column grant, because a trigger function is not
-- subject to the caller's column privileges.
revoke update on public.workspaces from authenticated;
grant update (name, slug, metadata, personal) on public.workspaces to authenticated;

comment on column public.workspaces.owner_id is
  'Owner of the workspace. Not writable by the authenticated role (migration '
  '0019): ownership is a tenancy boundary, not a delegated permission.';

-- ---------------------------------------------------------------------------
-- 2. RLS: an admin may not reach an owner row.
-- ---------------------------------------------------------------------------
-- The new predicate is "I manage this workspace, and this row is not the
-- owner's — unless the owner's row is mine". `can_manage_workspace` keeps its
-- meaning; this narrows the *rows* it applies to.
--
-- `using` and `with check` differ on purpose:
--   * `using`     — which rows may be selected for update/deletion,
--   * `with check` — what the row may look like afterwards. Both halves are
--     needed: `using` alone would let an admin rewrite a member row into
--     `role='owner'`, because the *current* row is not an owner row.
-- The owner branch in `with check` is written against `owner_id` read from
-- `workspaces` so that it does not depend on the row's own (now changed) role.
drop policy if exists workspace_members_update_manage on public.workspace_members;

create policy workspace_members_update_manage on public.workspace_members
  for update using (
    public.can_manage_workspace(workspace_id)
    and (
      user_id = auth.uid()
      or not exists (
        select 1
        from public.workspace_members owner_row
        where owner_row.workspace_id = workspace_members.workspace_id
          and owner_row.user_id = workspace_members.user_id
          and owner_row.role = 'owner'
      )
    )
  )
  with check (
    public.can_manage_workspace(workspace_id)
    and (
      -- A row may only *become* (or stay) the owner row through the service
      -- role or a definer function, never through a user request.
      role <> 'owner'
      or exists (
        select 1
        from public.workspaces w
        where w.id = workspace_members.workspace_id
          and w.owner_id = workspace_members.user_id
          and w.owner_id = auth.uid()
      )
    )
  );

comment on policy workspace_members_update_manage on public.workspace_members is
  'Management may change roles, but never on the owner row and never into the '
  'owner role. Ownership transfer is not a member-role change (migration 0019).';

drop policy if exists workspace_members_delete_manage_or_self on public.workspace_members;

create policy workspace_members_delete_manage_or_self on public.workspace_members
  for delete using (
    (user_id = auth.uid() and role <> 'owner')
    or (
      public.can_manage_workspace(workspace_id)
      and not exists (
        select 1
        from public.workspaces w
        where w.id = workspace_members.workspace_id
          and w.owner_id = workspace_members.user_id
      )
    )
  );

comment on policy workspace_members_delete_manage_or_self on public.workspace_members is
  'A member may leave. A manager may remove non-owner members. An owner '
  'membership row cannot be deleted by anyone but the service role, because '
  'deleting it is how an admin used to take a workspace (migration 0019).';

drop policy if exists workspaces_update_manage on public.workspaces;

create policy workspaces_update_manage on public.workspaces
  for update using (public.can_manage_workspace(id))
  with check (
    public.can_manage_workspace(id)
    and owner_id = (
      select w.owner_id from public.workspaces w where w.id = workspaces.id
    )
  );

comment on policy workspaces_update_manage on public.workspaces is
  'A manager may edit workspace settings. `owner_id` is compared against the '
  'stored row so an update cannot move ownership (migration 0019).';

-- ---------------------------------------------------------------------------
-- 3. Trigger: the column-level guard, for anything that gets past layer 2.
-- ---------------------------------------------------------------------------
-- `auth.uid()` is NULL for the service role, for migrations, and inside a
-- SECURITY DEFINER function body unless the definer sets the claim — so "no
-- actor" means "trusted context" and is allowed through. This keeps the worker,
-- the signup trigger, seed data and provisioning working while making every
-- user-originated request subject to the rule.
create or replace function public.guard_workspace_ownership()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_actor uuid := auth.uid();
begin
  if v_actor is null then
    return coalesce(new, old);
  end if;

  if tg_table_name = 'workspaces' then
    if tg_op = 'UPDATE' and new.owner_id is distinct from old.owner_id then
      raise exception 'workspace ownership cannot be changed by a user request'
        using errcode = '42501',  -- insufficient_privilege
              hint = 'Ownership transfer must go through a service-role operation.';
    end if;
    return coalesce(new, old);
  end if;

  -- workspace_members
  if tg_op = 'UPDATE' then
    if old.role = 'owner' and v_actor <> old.user_id then
      raise exception 'the workspace owner''s membership cannot be modified'
        using errcode = '42501';
    end if;
    if new.role = 'owner' and v_actor <> new.user_id then
      raise exception 'ownership cannot be granted to another user'
        using errcode = '42501';
    end if;
    return new;
  end if;

  if tg_op = 'DELETE' and old.role = 'owner' and v_actor <> old.user_id then
    raise exception 'the workspace owner''s membership cannot be removed'
      using errcode = '42501';
  end if;

  return coalesce(new, old);
end;
$$;

drop trigger if exists trg_guard_workspace_ownership on public.workspaces;
create trigger trg_guard_workspace_ownership
  before update on public.workspaces
  for each row execute function public.guard_workspace_ownership();

drop trigger if exists trg_guard_member_ownership on public.workspace_members;
create trigger trg_guard_member_ownership
  before update or delete on public.workspace_members
  for each row execute function public.guard_workspace_ownership();

revoke execute on function public.guard_workspace_ownership() from public, anon, authenticated;
