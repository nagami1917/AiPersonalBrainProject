-- ============================================================================
-- 0003 — chunks: the retrieval and citation unit
-- ============================================================================
-- Dimension is pinned to 1536 (text-embedding-3-small) in one place:
-- public.embedding_dim() below. Changing the model is a migration, not a
-- config tweak (invariant 7). HNSW indexes cannot be built over a dynamic
-- dimension, so the dimension is also written literally here — both must move
-- together, and public.assert_embedding_dim() fails loudly if they diverge.
-- ============================================================================

create or replace function public.embedding_dim()
returns integer
language sql
immutable
as $$ select 1536 $$;

comment on function public.embedding_dim() is
  'Pinned embedding dimension. Change via migration together with the vector(1536) column and HNSW index.';

create table if not exists public.chunks (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  document_id     uuid not null references public.documents(id) on delete cascade,
  ordinal         integer not null check (ordinal >= 0),
  content         text not null check (char_length(content) > 0),
  token_count     integer check (token_count is null or token_count > 0),
  char_start      integer check (char_start is null or char_start >= 0),
  char_end        integer check (char_end is null or char_end >= 0),
  page_start      integer check (page_start is null or page_start >= 1),
  page_end        integer check (page_end is null or page_end >= 1),
  heading_path    text,
  embedding       vector(1536),
  embedding_model text not null,
  metadata        jsonb not null default '{}'::jsonb,
  version         integer not null default 1 check (version >= 1),
  created_at      timestamptz not null default now(),

  constraint chunks_char_range check (
    char_start is null or char_end is null or char_end >= char_start
  ),
  constraint chunks_page_range check (
    page_start is null or page_end is null or page_end >= page_start
  )
);

comment on column public.chunks.user_id is
  'Denormalized from documents so RLS and every retrieval filter avoid a join.';

-- idempotent reprocessing: one row per (document, position, version)
create unique index if not exists uq_chunks_document_ordinal_version
  on public.chunks (document_id, ordinal, version);

create index if not exists idx_chunks_user_document_ordinal
  on public.chunks (user_id, document_id, ordinal);

create index if not exists idx_chunks_document
  on public.chunks (document_id);

-- keyword arm of hybrid search
create index if not exists idx_chunks_fts
  on public.chunks using gin (to_tsvector('english', content));

-- vector arm of hybrid search
create index if not exists idx_chunks_embedding_hnsw
  on public.chunks using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- Guard: embedding dimension in code must match the column/index.
-- Called by the test suite (and optionally at migrate time) so a model swap
-- cannot silently produce unfindable rows.
-- ---------------------------------------------------------------------------
create or replace function public.assert_embedding_dim()
returns void
language plpgsql
stable
as $$
declare
  col_dim integer;
begin
  select atttypmod into col_dim
  from pg_attribute
  where attrelid = 'public.chunks'::regclass
    and attname = 'embedding';

  -- atttypmod for vector(n) is n
  if col_dim is distinct from public.embedding_dim() then
    raise exception
      'Embedding dimension mismatch: chunks.embedding is vector(%s) but embedding_dim() returns %s',
      col_dim, public.embedding_dim();
  end if;
end;
$$;
