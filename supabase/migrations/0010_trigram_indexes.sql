-- ============================================================================
-- 0010 — typo-tolerant search (pg_trgm)
-- ============================================================================
-- Kept separate from 0002/0004 for two reasons:
--
--   1. pg_trgm is a C extension. Some minimal/managed Postgres images ship
--      pgvector but not pg_trgm (Supabase ships both, so this is a non-issue in
--      production). Isolating it means `supabase db push` fails loudly on the
--      extension rather than after creating eleven tables.
--   2. If an environment genuinely cannot install it, this single migration is
--      the only thing to skip — every other feature keeps working, with search
--      suggestions degraded to exact prefix matching.
--
-- Used by:
--   GET /v1/search/suggest   → title prefix / typo tolerance
--   entity merge suggestions → near-duplicate entity names
-- ============================================================================

create extension if not exists "pg_trgm";

-- title substring / suggestion search
create index if not exists idx_documents_title_trgm
  on public.documents using gin (title gin_trgm_ops);

-- entity name matching for merge suggestions
create index if not exists idx_entities_name_trgm
  on public.entities using gin (name gin_trgm_ops);

-- tag autocomplete
create index if not exists idx_tags_name_trgm
  on public.tags using gin (name gin_trgm_ops);
