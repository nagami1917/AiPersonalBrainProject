-- ============================================================================
-- 0010 — typo-tolerant search (pg_trgm)
-- ============================================================================
-- Kept in its own migration for two reasons:
--
--   1. pg_trgm is a C extension. Some minimal/managed Postgres images ship
--      pgvector but not pg_trgm (Supabase ships both). Isolating it means
--      `supabase db push` fails loudly on the extension rather than after
--      creating eighteen tables.
--   2. If an environment cannot install it, this is the only migration to skip:
--      every other feature keeps working, with suggestions degraded to exact
--      prefix matching.
--
-- Used by: GET /v1/search/suggest (titles), tag autocomplete, and entity merge
-- suggestions in the graph.
-- ============================================================================

create extension if not exists "pg_trgm";

-- note titles
create index if not exists idx_notes_title_trgm
  on public.notes using gin (title gin_trgm_ops);

-- document titles (filename-derived, so typos are common)
create index if not exists idx_documents_title_trgm
  on public.documents using gin (title gin_trgm_ops);

-- entity name matching for merge suggestions
create index if not exists idx_entities_name_trgm
  on public.knowledge_entities using gin (name gin_trgm_ops);

-- tag autocomplete
create index if not exists idx_tags_name_trgm
  on public.tags using gin (name gin_trgm_ops);
