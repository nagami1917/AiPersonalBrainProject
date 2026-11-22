-- ============================================================================
-- 0002 — collections, documents, tags
-- ============================================================================
-- documents is the single table for uploads AND notes (ADR-003).
--   source_type = 'upload'  → storage_path set
--   source_type = 'note'    → raw_text set, storage_path null
-- ============================================================================

-- ---------------------------------------------------------------------------
-- collections (hierarchical folders, depth enforced in the API)
-- ---------------------------------------------------------------------------
create table if not exists public.collections (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  parent_id   uuid references public.collections(id) on delete set null,
  name        text not null check (char_length(btrim(name)) between 1 and 120),
  description text,
  color       text,
  position    integer not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint collections_no_self_parent check (parent_id is null or parent_id <> id)
);

create index if not exists idx_collections_user_parent
  on public.collections (user_id, parent_id);

-- Sibling names must be unique (case-insensitive). coalesce() collapses "root
-- level" into a single bucket so root-level duplicates are also rejected.
create unique index if not exists uq_collections_sibling_name
  on public.collections (
    user_id,
    coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid),
    lower(btrim(name))
  );

drop trigger if exists trg_collections_updated_at on public.collections;
create trigger trg_collections_updated_at
  before update on public.collections
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- documents
-- ---------------------------------------------------------------------------
create table if not exists public.documents (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid not null references auth.users(id) on delete cascade,
  collection_id      uuid references public.collections(id) on delete set null,
  source_type        text not null default 'upload'
                       check (source_type in ('upload', 'note', 'conversation')),
  title              text not null check (char_length(btrim(title)) between 1 and 500),
  mime_type          text,
  storage_path       text,
  file_size          bigint check (file_size is null or file_size >= 0),
  checksum_sha256    text,
  raw_text           text,
  page_count         integer check (page_count is null or page_count >= 0),
  status             text not null default 'pending'
                       check (status in ('pending', 'processing', 'ready', 'failed', 'archived')),
  processing_stage   text check (processing_stage in ('extracting', 'chunking', 'embedding', 'entities')),
  error_message      text,
  version            integer not null default 1 check (version >= 1),
  embedding_model    text,
  summary            text,
  metadata           jsonb not null default '{}'::jsonb,
  deleted_at         timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),

  -- an upload must have a storage object; notes must not
  constraint documents_upload_has_storage check (
    (source_type = 'upload' and storage_path is not null)
    or (source_type <> 'upload')
  ),
  -- failed documents must explain themselves
  constraint documents_failure_has_message check (
    status <> 'failed' or error_message is not null
  )
);

comment on column public.documents.version is
  'Incremented on content edit; guards stale worker writes (see ADR/invariants).';
comment on column public.documents.processing_stage is
  'Honest progress for the UI — never a generic spinner.';

create index if not exists idx_documents_user_status_updated
  on public.documents (user_id, status, updated_at desc)
  where deleted_at is null;

create index if not exists idx_documents_user_collection
  on public.documents (user_id, collection_id)
  where deleted_at is null;

-- dedupe: a checksum may only exist once among live documents for a user
create unique index if not exists uq_documents_user_checksum
  on public.documents (user_id, checksum_sha256)
  where deleted_at is null and checksum_sha256 is not null;

-- document-level keyword search (title + body)
create index if not exists idx_documents_fts
  on public.documents
  using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(raw_text, '')));

-- stuck-job sweeper: find documents left mid-flight
create index if not exists idx_documents_in_flight
  on public.documents (user_id)
  where status in ('pending', 'processing') and deleted_at is null;

drop trigger if exists trg_documents_updated_at on public.documents;
create trigger trg_documents_updated_at
  before update on public.documents
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- tags
-- ---------------------------------------------------------------------------
create table if not exists public.tags (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  name       text not null check (char_length(btrim(name)) between 1 and 60),
  created_at timestamptz not null default now()
);

create unique index if not exists uq_tags_user_name
  on public.tags (user_id, lower(btrim(name)));

-- ---------------------------------------------------------------------------
-- document_tags
-- user_id is denormalized on purpose so the RLS policy is the standard
-- single-column form with no join (see DATABASE.md §4).
-- ---------------------------------------------------------------------------
create table if not exists public.document_tags (
  document_id uuid not null references public.documents(id) on delete cascade,
  tag_id      uuid not null references public.tags(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  created_at  timestamptz not null default now(),
  primary key (document_id, tag_id)
);

create index if not exists idx_document_tags_tag on public.document_tags (tag_id);
create index if not exists idx_document_tags_user on public.document_tags (user_id);
