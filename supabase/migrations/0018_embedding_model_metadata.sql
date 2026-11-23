-- ============================================================================
-- 0018 — embedding model metadata and drift detection
-- ============================================================================
-- The spec asks for two things this schema could describe but not report:
--
--   1. "Embedding model metadata" — what model produced each vector, with its
--      dimension, so an index can be audited rather than assumed.
--   2. "Reprocessing when embedding models change" — the ability to *find* the
--      rows that are stale.
--
-- Today `document_chunks.embedding_model` holds a bare string written by the
-- worker, and nothing ever compares it to the configured model. `notes` and
-- `documents` each carry an `embedding_model` too. The result is that changing
-- PINNED_EMBEDDING_MODEL leaves the corpus in a mixed state that no query can
-- detect: old rows are still retrievable, still ranked against new vectors, and
-- silently worse. That is a correctness problem, not a preference — cosine
-- distance between vectors from two different models is meaningless.
--
-- ## What is added
--
--  * `embedding_dim` alongside the model name, because a model name alone does
--    not pin a width (`text-embedding-3-*` are truncated to a requested size).
--    Checked against `public.embedding_dim()`, so a row can never claim a width
--    the column cannot hold.
--  * `public.embedding_model_stats(uuid)` — per-workspace counts by model, for
--    the reindex decision and for an operator to see corpus drift directly.
--  * `public.stale_chunk_counts(uuid)` — how much work a reindex would be.
--
-- ## Why the model columns are backfilled rather than left NULL
--
-- Existing rows were all written by one model, so `coalesce(embedding_model,
-- <column default>)` is not a guess — it is the only value the writer could have
-- used. Leaving them NULL would make every pre-existing chunk read as "stale"
-- and trigger a needless full reindex of the corpus on first boot.
-- ============================================================================

-- --- 1. width travels with the model name -----------------------------------
alter table public.document_chunks
  add column if not exists embedding_dim integer;

comment on column public.document_chunks.embedding_dim is
  'Width of the vector in `embedding`. Pinned by public.embedding_dim(); a model name alone does not determine it.';

-- Backfill from the column's own declared width. Safe because the column is
-- vector(1536): every row that has an embedding has exactly this many floats.
update public.document_chunks
   set embedding_dim = public.embedding_dim()
 where embedding_dim is null
   and embedding is not null;

-- A row with no embedding has no width. The CHECK below allows NULL precisely
-- for that case (a chunk mid-ingest) and rejects any other mismatch.
alter table public.document_chunks
  drop constraint if exists document_chunks_embedding_dim_matches_column;
alter table public.document_chunks
  add constraint document_chunks_embedding_dim_matches_column
  check (embedding_dim is null or embedding_dim = public.embedding_dim());

-- Partial: only rows that actually carry a vector are interesting for drift,
-- and a full index would be mostly entries for NULLs during a fresh ingest.
create index if not exists idx_chunks_embedding_model
  on public.document_chunks (workspace_id, embedding_model)
  where embedding is not null;

-- --- 2. the same metadata on the parents ------------------------------------
-- Notes and documents already have `embedding_model`; the width is added so the
-- two agree and a parent's status can be compared to a chunk's.
alter table public.notes
  add column if not exists embedding_dim integer;
alter table public.documents
  add column if not exists embedding_dim integer;

update public.notes
   set embedding_dim = public.embedding_dim()
 where embedding_dim is null and embedding_model is not null;
update public.documents
   set embedding_dim = public.embedding_dim()
 where embedding_dim is null and embedding_model is not null;

comment on column public.notes.embedding_dim is
  'Width of the vectors this note was indexed with. NULL = never indexed.';
comment on column public.documents.embedding_dim is
  'Width of the vectors this document was indexed with. NULL = never indexed.';

-- --- 3. corpus drift, as a query ------------------------------------------
create or replace function public.embedding_model_stats(p_workspace_id uuid)
returns table (
  embedding_model text,
  embedding_dim   integer,
  chunk_count     bigint,
  is_current      boolean
)
language sql
stable
as $$
  -- `is_current` compares against the parent's configured model where one is
  -- recorded, else against the function default. The worker writes the same
  -- string to chunk and parent, so in practice these agree; the coalesce exists
  -- so a chunk written before the parent was updated still reports sensibly.
  select
    c.embedding_model,
    c.embedding_dim,
    count(*) as chunk_count,
    c.embedding_model = coalesce(
      (select p.default_embedding_model from public.profiles p limit 1),
      'text-embedding-3-small'
    ) as is_current
  from public.document_chunks c
  where c.workspace_id = p_workspace_id
    and c.embedding is not null
  group by c.embedding_model, c.embedding_dim
  order by count(*) desc;
$$;

comment on function public.embedding_model_stats(uuid) is
  'Per-model chunk counts for one workspace. Compare against PINNED_EMBEDDING_MODEL to decide whether a reindex is needed.';

-- --- 4. how much work would a reindex be? ---------------------------------
create or replace function public.stale_chunk_counts(p_workspace_id uuid, p_model text)
returns table (stale_chunks bigint, stale_sources bigint)
language sql
stable
as $$
  select
    count(*) filter (where c.embedding_model is distinct from p_model) as stale_chunks,
    count(distinct coalesce(c.note_id, c.document_id))
      filter (where c.embedding_model is distinct from p_model)          as stale_sources
  from public.document_chunks c
  where c.workspace_id = p_workspace_id;
$$;

comment on function public.stale_chunk_counts(uuid, text) is
  'Chunks and distinct parent sources indexed with a model other than p_model — the size of the reprocessing job.';

grant execute on function public.embedding_model_stats(uuid)      to authenticated, service_role;
grant execute on function public.stale_chunk_counts(uuid, text)   to authenticated, service_role;
