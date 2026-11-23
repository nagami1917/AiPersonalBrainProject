-- ============================================================================
-- 0014 — owners can see their own workspace (INSERT ... RETURNING fix)
-- ============================================================================
-- BUG: creating a workspace returned HTTP 500
--      `InsufficientPrivilegeError: new row violates row-level security policy
--       for table "workspaces"`
--
-- The INSERT policy was correct (`with check (owner_id = auth.uid())`) and the
-- row it was asked to write was correct. The failure came from a policy that is
-- not involved in the write at all: **`RETURNING` requires a SELECT policy that
-- can see the new row**, and the only SELECT policy was
--
--     for select using (public.is_workspace_member(id))
--
-- `is_workspace_member()` answers "is the caller a member of this workspace?"
-- by looking for a row in `workspace_members`. For a workspace that is being
-- created, that row does not exist yet — the API inserts it *after* the
-- workspace, and the route needs `RETURNING *` to get the id to do so. So the
-- read-back was evaluated against a membership that could not exist, the tuple
-- was filtered out, and Postgres reported it as an RLS violation on the insert.
--
-- ## Why the fix is in the policy and not in the query
--
-- Rewriting the route to generate the id itself (which it partly does) and skip
-- `RETURNING` would work around this one call site. It would leave the trap in
-- place for the next `insert ... returning`, and it would keep the database
-- asserting something untrue: an owner cannot currently read the workspace they
-- own unless a membership row happens to exist.
--
-- The policy now states what is actually intended — you can see a workspace you
-- belong to, **or one you own** — which also removes an ordering dependency
-- between two tables that share a lifecycle. Ownership is not a tenancy leak:
-- `workspaces.owner_id` is set from `auth.uid()` by the INSERT policy, so this
-- grants sight of a row the same user just created or already owns.
--
-- Everything else is unchanged: a non-member still sees nothing, and
-- `is_workspace_member()` still governs the team case, where one member reads
-- another's workspace through membership rather than ownership.
--
-- Regression tests: `tests/db/test_api_workspaces.py` — creating a workspace,
-- reading it back, and a non-owner being unable to read it.
-- ============================================================================

drop policy if exists workspaces_select_member on public.workspaces;

create policy workspaces_select_member on public.workspaces
  for select using (
    owner_id = auth.uid() or public.is_workspace_member(id)
  );

comment on policy workspaces_select_member on public.workspaces is
  'Owner or member may read a workspace. Owner is included because INSERT ... '
  'RETURNING needs a SELECT policy that can see the row just written, and the '
  'membership row is created after the workspace (migration 0014).';
