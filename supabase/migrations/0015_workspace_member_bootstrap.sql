-- ============================================================================
-- 0015 — an owner can insert the first membership row of a new workspace
-- ============================================================================
-- BUG (found immediately after 0014): creating a workspace still failed, now on
-- the *second* statement of the same route:
--
--     asyncpg.exceptions.InsufficientPrivilegeError:
--       new row violates row-level security policy for table "workspace_members"
--
--     insert into public.workspace_members (workspace_id, user_id, role)
--     values ($1, $2, 'owner')
--
-- The INSERT policy was
--
--     with check (public.can_manage_workspace(workspace_id))
--
-- and `can_manage_workspace()` asks "does the caller hold role owner/admin in
-- this workspace?" — by reading `workspace_members`. So the first row of a new
-- workspace had to exist before it could be written. Nothing could ever create
-- a workspace through the API; there was no way in.
--
-- ## Why signup never exposed this
--
-- `public.handle_new_user()` is SECURITY DEFINER: it runs as the function owner,
-- so it bypasses RLS entirely and creates the personal workspace and its owner
-- row without consulting a policy. Every seeded user therefore had a workspace,
-- and the only path that *needed* a policy — an explicit `POST /workspaces` —
-- was never exercised by a test. It is worth being explicit that these two bugs
-- (0014, 0015) were both invisible to the existing suite for this reason.
--
-- ## The rule being stated
--
-- Management rights are delegated membership. Ownership is not: it comes from
-- `workspaces.owner_id`, which the workspace INSERT policy pins to `auth.uid()`.
-- So a caller may insert a row that makes *themselves* the owner of a workspace
-- *they own*, and nothing else. That is exactly the bootstrap, and it cannot be
-- borrowed to reach someone else's workspace:
--
--   * `user_id = auth.uid()` — you cannot make anyone else a member,
--   * `role = 'owner'`       — you cannot self-grant a lesser role and then
--                              lean on `can_manage_workspace` for the rest,
--   * `w.owner_id = auth.uid()` — the workspace must already be yours.
--
-- The `exists` subquery reads `public.workspaces`, which is itself under RLS;
-- it is satisfied by the owner branch added in 0014, so the two migrations are
-- a pair and 0015 depends on 0014.
--
-- Delegation to other users is unchanged: `can_manage_workspace()` still governs
-- every invite and role change, so an owner or admin adds people, and no one
-- else can.
--
-- Regression tests: `tests/db/test_api_workspaces.py` — creation returns 201 and
-- the caller is the owner member; a non-owner cannot add themselves.
-- ============================================================================

drop policy if exists workspace_members_insert_manage on public.workspace_members;

create policy workspace_members_insert_manage on public.workspace_members
  for insert with check (
    public.can_manage_workspace(workspace_id)
    or (
      user_id = auth.uid()
      and role = 'owner'
      and exists (
        select 1
        from public.workspaces w
        where w.id = workspace_members.workspace_id
          and w.owner_id = auth.uid()
      )
    )
  );

comment on policy workspace_members_insert_manage on public.workspace_members is
  'Management may add members. Additionally an owner may insert their own '
  'first owner row into a workspace they own, so a new workspace can be '
  'bootstrapped (migration 0015).';
