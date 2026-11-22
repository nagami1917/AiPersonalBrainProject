-- ============================================================================
-- 0013 — audit log for sensitive workspace operations
-- ============================================================================
-- Security architecture §7, rule 10. Before this table, "who deleted the
-- document" was answerable only from application logs, which rotate, and from
-- `documents.uploaded_by`, which says nothing about a deletion.
--
-- Design choices worth stating:
--
--   * **Append-only.** No update or delete policy for any role. The service role
--     could still do it, but nothing in the codebase does, and a row that can be
--     rewritten is not evidence.
--   * **Readable by workspace admins, not by members.** Knowing that a document
--     was deleted is not sensitive; knowing that a specific colleague removed
--     another colleague's access is. `can_manage_workspace` gates it.
--   * **No document contents, no secrets.** `detail` is jsonb and is written by
--     `app/core/audit.py`, which whitelists field names. The point of rule 10's
--     counterpart (rule on logging) is that the audit trail must not become the
--     place private text leaks to.
--   * **Actor is nullable with ON DELETE SET NULL.** A deleted account should
--     not erase the history of what it did — the row survives with the id gone.
-- ============================================================================

create table if not exists public.audit_events (
  id            bigint generated always as identity primary key,
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  actor_id      uuid references auth.users(id) on delete set null,

  -- Dotted, stable, and greppable: 'workspace.member.add', 'document.delete'.
  -- A free-text action column becomes a different vocabulary in every route.
  action        text not null check (length(action) between 3 and 80),

  -- What was acted on. Deliberately no FK: the row must outlive the resource it
  -- describes, which is the entire reason an audit trail exists.
  target_type   text check (target_type in (
                  'workspace','member','document','note','collection','conversation','entity'
                )),
  target_id     uuid,
  target_label  text check (target_label is null or length(target_label) <= 200),

  detail        jsonb not null default '{}'::jsonb,
  request_id    text,
  created_at    timestamptz not null default now()
);

-- The two access patterns: "recent activity in this workspace" and "what
-- happened to this thing".
create index if not exists idx_audit_workspace_created
  on public.audit_events (workspace_id, created_at desc);

create index if not exists idx_audit_target
  on public.audit_events (workspace_id, target_type, target_id, created_at desc);

create index if not exists idx_audit_actor
  on public.audit_events (workspace_id, actor_id, created_at desc);

alter table public.audit_events enable row level security;

-- Reads: workspace admins and owners.
drop policy if exists audit_events_select_manage on public.audit_events;
create policy audit_events_select_manage on public.audit_events
  for select to authenticated
  using (public.can_manage_workspace(workspace_id));

-- Writes: through the API only. No insert policy for `authenticated`, so a
-- user cannot forge an entry that says someone else did something — the
-- API writes via `record_audit`, which is SECURITY DEFINER and takes the actor
-- from the JWT, never from a parameter.
create or replace function public.record_audit(
  p_workspace_id uuid,
  p_action       text,
  p_target_type  text default null,
  p_target_id    uuid default null,
  p_target_label text default null,
  p_detail       jsonb default '{}'::jsonb,
  p_request_id   text default null
)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_actor uuid;
  v_id    bigint;
begin
  -- The actor is whoever the request is authenticated as. It is read from the
  -- same GUC that RLS uses, so it cannot be supplied by the caller.
  v_actor := nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;

  if v_actor is null then
    raise exception 'audit events require an authenticated actor'
      using errcode = '42501';
  end if;

  -- Recording an event about a workspace the actor is not in is not a thing
  -- that should be possible, even from a compromised route.
  --
  -- Tested directly rather than through public.is_workspace_member(): that
  -- helper answers "may I read this workspace", which is false inside a
  -- SECURITY DEFINER function whose current_user is the owner, not the caller.
  -- The caller's id is in the GUC, so this compares against the same value RLS
  -- uses and cannot be spoofed by a parameter.
  if not exists (
    select 1 from public.workspace_members wm
    where wm.workspace_id = p_workspace_id and wm.user_id = v_actor
  ) then
    raise exception 'not a member of workspace %', p_workspace_id
      using errcode = '42501';
  end if;

  insert into public.audit_events
    (workspace_id, actor_id, action, target_type, target_id, target_label, detail, request_id)
  values
    (p_workspace_id, v_actor, p_action, p_target_type, p_target_id,
     left(p_target_label, 200), coalesce(p_detail, '{}'::jsonb), p_request_id)
  returning id into v_id;

  return v_id;
end;
$$;

comment on function public.record_audit(uuid, text, text, uuid, text, jsonb, text) is
  'Append-only audit entry; actor comes from the JWT claim, never a parameter.';

revoke all on function public.record_audit(uuid, text, text, uuid, text, jsonb, text) from public;
grant execute on function public.record_audit(uuid, text, text, uuid, text, jsonb, text) to authenticated;
