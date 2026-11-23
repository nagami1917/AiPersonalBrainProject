-- ============================================================================
-- ownership_guard.sql — a workspace admin must not be able to become the owner
-- ============================================================================
-- Regression test for migration 0019. Every "attack" below was a real, working
-- session against a database at migration 0018; three statements were enough
-- for an invited admin to take over the tenant:
--
--     update workspace_members set role='viewer' where user_id = <the owner>;
--     update workspace_members set role='owner'  where user_id = <me>;
--     update workspaces set owner_id = <me> where id = <the workspace>;
--
-- This file is written to FAIL LOUDLY on 0018 and to pass on 0019. Each attack
-- runs inside a sub-block so that if it *succeeds* the whole statement raises
-- and the transaction unwinds — the fixtures are never left corrupted by a
-- regression, and the failure is an assertion rather than a confusing later
-- error.
--
-- The attacks assert on two things, not one:
--   * the statement raised `insufficient_privilege` (42501), OR
--   * the statement was a silent no-op (0 rows affected, which is what RLS does
--     for a filter it refuses).
-- Either outcome is "refused". A `SECURITY FAILURE` means neither happened.
--
--   psql -v ON_ERROR_STOP=1 -d nexus -f supabase/tests/ownership_guard.sql
-- ============================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------
-- Fixtures: `adm` is an admin in `own`'s workspace, `mem` is a plain member.
-- Names are lowercase and short so the DO blocks below stay readable.
-- ---------------------------------------------------------------------------
insert into auth.users (id, email, raw_user_meta_data) values
  ('a0000000-0000-0000-0000-000000000001','guard-owner@nexus.test','{"full_name":"Guard Owner"}'),
  ('a0000000-0000-0000-0000-000000000002','guard-admin@nexus.test','{"full_name":"Guard Admin"}'),
  ('a0000000-0000-0000-0000-000000000003','guard-member@nexus.test','{"full_name":"Guard Member"}')
on conflict (id) do nothing;

-- Note on privileges, deliberately: this file does NOT re-assert the column
-- grant from migration 0019 before attacking. `tenancy.sql` hands `authenticated`
-- a blanket UPDATE on every public table, so in CI these attacks run with *more*
-- privilege than production has. That is intentional — it proves the RLS policy
-- and the trigger stop the escalation on their own, rather than being carried by
-- a grant that could be loosened by a future migration. The grant itself is
-- asserted separately below (layer 3), where its absence is the thing checked.

create temp table if not exists guard_ids (k text primary key, v uuid);
truncate guard_ids;

do $$
declare
  v_ws uuid;
begin
  select id into v_ws from public.workspaces
   where owner_id = 'a0000000-0000-0000-0000-000000000001' limit 1;

  insert into guard_ids values ('ws', v_ws) on conflict (k) do update set v = excluded.v;

  delete from public.workspace_members
   where workspace_id = v_ws and user_id in (
     'a0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000003');

  insert into public.workspace_members (workspace_id, user_id, role) values
    (v_ws, 'a0000000-0000-0000-0000-000000000002', 'admin'),
    (v_ws, 'a0000000-0000-0000-0000-000000000003', 'member');
end $$;

\echo ''
\echo '=== attacks: each must be refused ==='

-- --- A1: the admin demotes the real owner (frees the owner slot) ------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    update public.workspace_members set role = 'viewer'
     where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000001';
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows > 0 then
      raise exception 'SECURITY FAILURE A1: an admin demoted the workspace owner (% rows)', v_rows;
    end if;
    raise notice 'A1 refused (no-op): an admin cannot demote the owner';
  exception when insufficient_privilege then
    raise notice 'A1 refused (42501): an admin cannot demote the owner';
  end;
end $$;

-- --- A2: the admin promotes itself to owner --------------------------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    update public.workspace_members set role = 'owner'
     where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000002';
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows > 0 then
      raise exception 'SECURITY FAILURE A2: an admin promoted itself to owner (% rows)', v_rows;
    end if;
    raise notice 'A2 refused (no-op): an admin cannot promote itself to owner';
  exception when insufficient_privilege then
    raise notice 'A2 refused (42501): an admin cannot promote itself to owner';
  end;
end $$;

-- --- A3: the admin rewrites workspaces.owner_id ----------------------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    update public.workspaces set owner_id = 'a0000000-0000-0000-0000-000000000002'
     where id = v_ws;
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows > 0 then
      raise exception 'SECURITY FAILURE A3: an admin rewrote owner_id (% rows)', v_rows;
    end if;
    raise notice 'A3 refused (no-op): owner_id is not writable';
  exception when insufficient_privilege then
    raise notice 'A3 refused (42501): owner_id is not writable';
  end;
end $$;

-- --- A4: the admin deletes the owner's membership row ----------------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    delete from public.workspace_members
     where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000001';
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows > 0 then
      raise exception 'SECURITY FAILURE A4: an admin deleted the owner membership (% rows)', v_rows;
    end if;
    raise notice 'A4 refused (no-op): an admin cannot delete the owner membership';
  exception when insufficient_privilege then
    raise notice 'A4 refused (42501): an admin cannot delete the owner membership';
  end;
end $$;

-- --- A5: a plain member promotes itself to admin --------------------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000003', true);
    update public.workspace_members set role = 'admin'
     where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000003';
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows > 0 then
      raise exception 'SECURITY FAILURE A5: a member promoted itself to admin (% rows)', v_rows;
    end if;
    raise notice 'A5 refused (no-op): a member cannot promote itself';
  exception when insufficient_privilege then
    raise notice 'A5 refused (42501): a member cannot promote itself';
  end;
end $$;

-- --- A6: the admin hands ownership to a third party ----------------------
-- The subtle half of the policy: `using` sees a *member* row, so only the
-- `with check` half can catch the row becoming the owner row.
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    update public.workspace_members set role = 'owner'
     where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000003';
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows > 0 then
      raise exception 'SECURITY FAILURE A6: an admin granted ownership to another user (% rows)', v_rows;
    end if;
    raise notice 'A6 refused (no-op): an admin cannot grant ownership to another user';
  exception when insufficient_privilege then
    raise notice 'A6 refused (42501): an admin cannot grant ownership to another user';
  end;
end $$;

\echo ''
\echo '=== no attack changed anything ==='
do $$
declare
  v_ws uuid;
  v_owner uuid;
  v_owner_role text;
  v_admin_role text;
begin
  select v into v_ws from guard_ids where k = 'ws';

  select owner_id into v_owner from public.workspaces where id = v_ws;
  select role into v_owner_role from public.workspace_members
   where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000001';
  select role into v_admin_role from public.workspace_members
   where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000002';

  if v_owner <> 'a0000000-0000-0000-0000-000000000001' then
    raise exception 'SECURITY FAILURE: owner_id changed to %', v_owner;
  end if;
  if v_owner_role is distinct from 'owner' then
    raise exception 'SECURITY FAILURE: the owner''s role is now %', v_owner_role;
  end if;
  if v_admin_role is distinct from 'admin' then
    raise exception 'SECURITY FAILURE: the admin''s role is now %', v_admin_role;
  end if;
  raise notice 'state intact: owner=owner, admin=admin, owner_id unchanged';
end $$;

\echo ''
\echo '=== legitimate operations: each must still work ==='

-- --- L1: an admin manages a member's role (delegated management) -----------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    update public.workspace_members set role = 'viewer'
     where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000003';
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows <> 1 then
      raise exception 'REGRESSION L1: an admin could not change a member''s role (% rows)', v_rows;
    end if;
    raise notice 'L1 works: an admin manages other members'' roles';
  end;
end $$;

-- --- L2: an admin removes a non-owner member ------------------------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    delete from public.workspace_members
     where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000003';
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows <> 1 then
      raise exception 'REGRESSION L2: an admin could not remove a member (% rows)', v_rows;
    end if;
    raise notice 'L2 works: an admin removes a non-owner member';
  end;
end $$;

-- --- L3: the owner renames the workspace (name stays writable) ------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000001', true);
    update public.workspaces set name = 'Renamed By Owner' where id = v_ws;
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows <> 1 then
      raise exception 'REGRESSION L3: the owner could not rename the workspace (% rows)', v_rows;
    end if;
    raise notice 'L3 works: an owner renames the workspace';
  end;
end $$;

-- --- L4: the admin renames the workspace --------------------------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    update public.workspaces set name = 'Renamed By Admin' where id = v_ws;
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows <> 1 then
      raise exception 'REGRESSION L4: an admin could not rename the workspace (% rows)', v_rows;
    end if;
    raise notice 'L4 works: an admin renames the workspace';
  end;
end $$;

-- --- L5: a member leaves on their own ----------------------------------
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    set local role authenticated;
    perform set_config('request.jwt.claim.sub','a0000000-0000-0000-0000-000000000002', true);
    delete from public.workspace_members
     where workspace_id = v_ws and user_id = 'a0000000-0000-0000-0000-000000000002';
    get diagnostics v_rows = row_count;
    reset role;
    if v_rows <> 1 then
      raise exception 'REGRESSION L5: an admin could not leave the workspace (% rows)', v_rows;
    end if;
    raise notice 'L5 works: an admin leaves on their own';
  end;
end $$;

-- --- L6: trusted context (no JWT claim) may still transfer ownership ----
-- The trigger is keyed on the *actor*, not the target. A service-role or
-- SECURITY DEFINER operation has no `request.jwt.claim.sub`, so provisioning and
-- a future reviewed transfer function are not blocked by a guard meant for
-- browsers.
do $$
declare v_ws uuid; v_rows int;
begin
  select v into v_ws from guard_ids where k = 'ws';
  begin
    update public.workspaces set owner_id = 'a0000000-0000-0000-0000-000000000001'
     where id = v_ws;
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception 'REGRESSION L6: trusted context could not write owner_id (% rows)', v_rows;
    end if;
    raise notice 'L6 works: trusted context (service role) can still set ownership';
  end;
end $$;

-- --- L7: layer 3 — the browser has no UPDATE on the ownership column ------
-- Checked as a catalogue assertion rather than by attempting the write, because
-- `tenancy.sql` has just granted blanket table UPDATE for its own purposes. In a
-- real deployment the grant is what makes layers 1-2 belt-and-braces; here what
-- is verified is that the migration *did* replace it, so a fresh production
-- database never relies on a policy alone.
do $$
declare
  v_table_level boolean;
  v_cols text;
begin
  select exists (
    select 1 from information_schema.role_table_grants
     where table_schema = 'public' and table_name = 'workspaces'
       and grantee = 'authenticated' and privilege_type = 'UPDATE'
  ) into v_table_level;

  select coalesce(string_agg(column_name, ',' order by column_name), '')
    into v_cols
    from information_schema.column_privileges
   where table_schema = 'public' and table_name = 'workspaces'
     and grantee = 'authenticated' and privilege_type = 'UPDATE';

  raise notice 'L7: authenticated UPDATE on workspaces — table-level=%, columns=[%]',
    v_table_level, v_cols;

  if position('owner_id' in v_cols) > 0 then
    raise exception 'SECURITY FAILURE L7: authenticated still has UPDATE on workspaces.owner_id';
  end if;
end $$;

-- Clean up so a re-run starts from the same place.
delete from public.workspace_members
 where workspace_id = (select v from guard_ids where k = 'ws')
   and user_id in ('a0000000-0000-0000-0000-000000000002','a0000000-0000-0000-0000-000000000003');

\echo ''
\echo '=== ownership_guard.sql: all assertions passed ==='
