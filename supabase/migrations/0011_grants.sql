-- ============================================================================
-- 0011 — explicit privileges
-- ============================================================================
-- Supabase's default privileges already make public tables reachable by
-- anon/authenticated/service_role. That default is convenient and invisible.
-- This migration makes the access model explicit and reviewable, so a table can
-- never become reachable merely because someone created it.
--
-- Model:
--   anon          → nothing. There is no public data in NEXUS.
--   authenticated → DML on workspace tables, with RLS deciding WHICH rows.
--                   processing_jobs is read-only for users; the worker owns
--                   writes through service_role.
--   service_role  → everything (bypasses RLS by Supabase design; restricted by
--                   convention to the worker and system operations).
-- ============================================================================

grant usage on schema public to anon, authenticated, service_role;

-- --- workspace data: RLS decides the rows -----------------------------------
grant select, insert, update, delete on
  public.workspaces,
  public.workspace_members,
  public.collections,
  public.notes,
  public.documents,
  public.tags,
  public.note_tags,
  public.document_tags,
  public.document_chunks,
  public.knowledge_entities,
  public.chunk_entities,
  public.knowledge_links,
  public.conversations,
  public.chat_messages,
  public.message_sources
to authenticated;

grant select, insert, update on public.profiles to authenticated;

-- --- user-visible but system-written ----------------------------------------
grant select on public.processing_jobs to authenticated;
grant select on public.usage_events   to authenticated;

-- --- service role: the worker ------------------------------------------------
grant all on
  public.profiles,
  public.workspaces,
  public.workspace_members,
  public.collections,
  public.notes,
  public.documents,
  public.tags,
  public.note_tags,
  public.document_tags,
  public.document_chunks,
  public.knowledge_entities,
  public.chunk_entities,
  public.knowledge_links,
  public.conversations,
  public.chat_messages,
  public.message_sources,
  public.processing_jobs,
  public.usage_events
to service_role;

-- --- storage -----------------------------------------------------------------
grant select, insert, update, delete on storage.objects to authenticated;
grant all on storage.objects to service_role;
grant select on storage.buckets to authenticated, service_role;

-- --- functions ---------------------------------------------------------------
-- Retrieval runs as the signed-in user, so RLS applies inside it.
grant execute on function public.hybrid_search(
  uuid, text, vector, integer, integer, double precision, double precision,
  integer, uuid, timestamptz, timestamptz, double precision, double precision,
  uuid[], text
) to authenticated, service_role;

revoke all on function public.hybrid_search(
  uuid, text, vector, integer, integer, double precision, double precision,
  integer, uuid, timestamptz, timestamptz, double precision, double precision,
  uuid[], text
) from anon;

-- Membership helpers must be callable by any authenticated policy evaluation.
grant execute on function public.workspace_role(uuid)          to authenticated, service_role;
grant execute on function public.is_workspace_member(uuid)     to authenticated, service_role;
grant execute on function public.can_write_workspace(uuid)     to authenticated, service_role;
grant execute on function public.can_manage_workspace(uuid)    to authenticated, service_role;
grant execute on function public.shares_workspace(uuid)        to authenticated, service_role;
grant execute on function public.embedding_dim()               to authenticated, service_role;
grant execute on function public.assert_embedding_dim()        to authenticated, service_role;

-- enqueue_job is callable by signed-in users because the API runs on the user's
-- connection (RLS, not the service key). The function is SECURITY DEFINER but
-- checks can_write_workspace() first, so execute permission is not a write grant.
revoke all on function public.enqueue_job(uuid, text, uuid, uuid, jsonb, integer, timestamptz)
  from public, anon;
grant execute on function public.enqueue_job(uuid, text, uuid, uuid, jsonb, integer, timestamptz)
  to authenticated, service_role;

revoke all on function public.refresh_entity_mentions(uuid) from public, anon, authenticated;
grant execute on function public.refresh_entity_mentions(uuid) to service_role;
