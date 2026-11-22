-- ============================================================================
-- 0011 — explicit privileges
-- ============================================================================
-- Supabase configures default privileges so that tables created in `public` are
-- already reachable by anon/authenticated/service_role. That default is
-- convenient and invisible. This migration makes the access model explicit and
-- reviewable, so a table can never become reachable merely because someone
-- created it.
--
-- Model:
--   anon          → nothing. There is no public data in NEXUS.
--   authenticated → full DML on user tables, with RLS deciding WHICH rows.
--                   jobs and usage_events are read-only for users; the worker
--                   owns writes through service_role.
--   service_role  → everything (bypasses RLS by Supabase design; restricted by
--                   convention to the worker and system operations).
-- ============================================================================

grant usage on schema public to anon, authenticated, service_role;

-- --- user data: RLS decides the rows -----------------------------------------
grant select, insert, update, delete on
  public.profiles,
  public.collections,
  public.documents,
  public.tags,
  public.document_tags,
  public.chunks,
  public.entities,
  public.chunk_entities,
  public.entity_edges,
  public.conversations,
  public.messages,
  public.message_citations
to authenticated;

-- --- user-visible but system-written ----------------------------------------
grant select on public.jobs          to authenticated;
grant select on public.usage_events  to authenticated;

-- --- service role: the worker -------------------------------------------------
grant all on
  public.profiles,
  public.collections,
  public.documents,
  public.tags,
  public.document_tags,
  public.chunks,
  public.entities,
  public.chunk_entities,
  public.entity_edges,
  public.conversations,
  public.messages,
  public.message_citations,
  public.jobs,
  public.usage_events
to service_role;

-- --- storage -----------------------------------------------------------------
grant select, insert, update, delete on storage.objects to authenticated;
grant all on storage.objects to service_role;
grant select on storage.buckets to authenticated, service_role;

-- --- functions ---------------------------------------------------------------
-- Retrieval is called by the API as the signed-in user (RLS applies inside it).
grant execute on function public.hybrid_search(
  uuid, text, vector, integer, integer, double precision, double precision,
  integer, uuid, uuid[], timestamptz, timestamptz, double precision, double precision
) to authenticated, service_role;

grant execute on function public.embedding_dim()    to authenticated, service_role;
grant execute on function public.assert_embedding_dim() to authenticated, service_role;

-- Enqueue and mention refresh are internal: service role only.
revoke all on function public.enqueue_job(uuid, text, uuid, jsonb, integer, timestamptz) from public, anon, authenticated;
grant execute on function public.enqueue_job(uuid, text, uuid, jsonb, integer, timestamptz) to service_role;

revoke all on function public.refresh_entity_mentions(uuid) from public, anon, authenticated;
grant execute on function public.refresh_entity_mentions(uuid) to service_role;

-- anon must not be able to call anything that touches user data
revoke all on function public.hybrid_search(
  uuid, text, vector, integer, integer, double precision, double precision,
  integer, uuid, uuid[], timestamptz, timestamptz, double precision, double precision
) from anon;
