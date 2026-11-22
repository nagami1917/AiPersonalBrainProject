-- ============================================================================
-- 0003 — document_chunks: the retrieval and citation unit
-- ============================================================================
-- One row = one embeddable, quotable passage, extracted from exactly one note
-- OR one document. The XOR constraint is the whole point of this table's shape:
-- no polymorphic source_id, no dangling reference, no ambiguous join.
--
-- Dimension is pinned to 1536 (text-embedding-3-small) in one place:
-- public.embedding_dim(). Changing the model is a migration, not a config tweak.
-- HNSW cannot be built over a dynamic dimension, so the column is written
-- literally and assert_embedding_dim() fails loudly if the two diverge.
-- ============================================================================

create or replace function public.embedding_dim()
returns integer
language sql
immutable
as $$ select 1536 $$;

comment on function public.embedding_dim() is
  'Pinned embedding dimension. Change via migration together with the vector(1536) column and HNSW index.';

create table if not exists public.document_chunks (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,

  -- exactly one of these is non-null (see constraint below)
  note_id         uuid references public.notes(id) on delete cascade,
  document_id     uuid references public.documents(id) on delete cascade,

  chunk_index     integer not null check (chunk_index >= 0),
  content         text not null check (char_length(content) > 0),
  token_count     integer check (token_count is null or token_count > 0),
  char_start      integer check (char_start is null or char_start >= 0),
  char_end        integer check (char_end is null or char_end >= 0),
  -- page_number is the citation anchor a reader actually checks ("p. 4").
  -- page_end is kept because a chunk may straddle a page break.
  page_number     integer check (page_number is null or page_number >= 1),
  page_end        integer check (page_end is null or page_end >= 1),
  heading_path    text,
  embedding       vector(1536),
  embedding_model text not null,
  metadata        jsonb not null default '{}'::jsonb,
  version         integer not null default 1 check (version >= 1),
  created_at      timestamptz not null default now(),

  -- THE constraint: a chunk has exactly one parent, and it must be a real row.
  constraint document_chunks_single_owner check (
    (note_id is not null and document_id is null)
    or (note_id is null and document_id is not null)
  ),
  constraint document_chunks_char_range check (
    char_start is null or char_end is null or char_end >= char_start
  ),
  constraint document_chunks_page_range check (
    page_number is null or page_end is null or page_end >= page_number
  )
);

-- Workspace consistency for both possible parents, structurally. The XOR check
-- guarantees the parent *column* is right; these guarantee the parent *row* is
-- in the same workspace as the chunk. A chunk can therefore never be filed under
-- workspace A while pointing at a note in workspace B.
alter table public.document_chunks
  drop constraint if exists document_chunks_note_workspace_fkey,
  add constraint document_chunks_note_workspace_fkey
    foreign key (note_id, workspace_id) references public.notes (id, workspace_id)
    on delete cascade,
  drop constraint if exists document_chunks_document_workspace_fkey,
  add constraint document_chunks_document_workspace_fkey
    foreign key (document_id, workspace_id) references public.documents (id, workspace_id)
    on delete cascade;

comment on constraint document_chunks_single_owner on public.document_chunks is
  'A chunk belongs to exactly one note or one document. Prevents orphaned chunks without a polymorphic FK.';

-- Target for composite FKs from chunk_entities and message_sources: a child row
-- can then prove it shares this chunk's workspace, not just its id.
alter table public.document_chunks
  add constraint document_chunks_id_workspace_key unique (id, workspace_id);

comment on column public.document_chunks.workspace_id is
  'Denormalized from the parent so RLS and every retrieval filter avoid a join.';

-- Idempotent reprocessing: one row per (parent, position, version). Two partial
-- unique indexes because the parent column is nullable per row.
create unique index if not exists uq_chunks_note_index_version
  on public.document_chunks (note_id, chunk_index, version)
  where note_id is not null;

create unique index if not exists uq_chunks_document_index_version
  on public.document_chunks (document_id, chunk_index, version)
  where document_id is not null;

create index if not exists idx_chunks_workspace_note
  on public.document_chunks (workspace_id, note_id, chunk_index);

create index if not exists idx_chunks_workspace_document
  on public.document_chunks (workspace_id, document_id, chunk_index);

-- keyword arm of hybrid search
create index if not exists idx_chunks_fts
  on public.document_chunks using gin (to_tsvector('english', content));

-- vector arm of hybrid search
create index if not exists idx_chunks_embedding_hnsw
  on public.document_chunks using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- Guard: the dimension in code must match the column.
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
  where attrelid = 'public.document_chunks'::regclass
    and attname = 'embedding';

  if col_dim is distinct from public.embedding_dim() then
    raise exception
      'Embedding dimension mismatch: document_chunks.embedding is vector(%s) but embedding_dim() returns %s',
      col_dim, public.embedding_dim();
  end if;
end;
$$;
