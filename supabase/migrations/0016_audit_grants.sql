-- ============================================================================
-- 0016 — explicit privileges for the audit trail
-- ============================================================================
-- 0011 ("explicit privileges") exists so that no table is reachable merely
-- because it was created. It listed every workspace table — and omitted
-- `audit_events`, which 0013 adds four migrations later.
--
-- That gap was invisible on Supabase, because Supabase sets default privileges
-- granting DML to `anon`/`authenticated` on tables in `public`. So `select from
-- audit_events` worked there without a grant, and worked for an unclear reason.
-- On a plain Postgres with an explicit-privilege prelude it fails with
--
--     InsufficientPrivilegeError: permission denied for table audit_events
--
-- which is what the database suite reported against `supabase/tests/prelude.sql`.
-- Depending on an implicit default is exactly what 0011 was written to prevent.
--
-- ## Why select and not insert
--
-- Read access is for the workspace roster endpoint (`GET /workspaces/{id}/audit`)
-- and, by policy, only managers see rows (`audit_events_select_manage`). Inserts
-- are deliberately **not** granted and there is deliberately no insert policy:
-- rows are written exclusively by `public.record_audit()`, which is SECURITY
-- DEFINER and takes the actor from the JWT claim rather than a parameter. Granting
-- insert here would create a second, forgeable write path.
--
-- No update or delete either — the trail is append-only, and a missing grant is a
-- second line of defence behind the missing policy.
-- ============================================================================

grant select on public.audit_events to authenticated;
grant all on public.audit_events to service_role;

comment on table public.audit_events is
  'Append-only audit trail. Written only through public.record_audit() (SECURITY '
  'DEFINER, actor from the JWT claim); authenticated has SELECT and no DML '
  '(migration 0016).';
