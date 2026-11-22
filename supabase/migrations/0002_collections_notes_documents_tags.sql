-- ============================================================================
-- 0002 — collections, notes, documents, tags
-- ============================================================================
-- Notes and documents are separate tables (Database Architecture §3.1). Both
-- are indexed into public.document_chunks, which is why that table carries a
-- CHECK-constrained single owner rather than a polymorphic source_id:
--
--   chunks.note_id      → ON DELETE CASCADE, nullable
--   chunks.document_id  → ON DELETE CASCADE, nullable
--   CHECK (note_id IS NOT NULL) <> (document_id IS NOT NULL)
--
-- Exactly one parent, enforced by the database, so a chunk can never outlive
-- the thing it was extracted from and no join is ever ambiguous.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- collections — shared hierarchy for both notes and documents
-- ---------------------------------------------------------------------------
create table if not exists public.collections (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  parent_id    uuid references public.collections(id) on delete set null,
  name         text not null check (char_length(btrim(name)) between 1 and 120),
  description  text,
  color        text,
  position     integer not null default 0,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint collections_no_self_parent check (parent_id is null or parent_id <> id)
);

create index if not exists idx_collections_workspace_parent
  on public.collections (workspace_id, parent_id);

create unique index if not exists uq_collections_sibling_name
  on public.collections (
    workspace_id,
    coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid),
    lower(btrim(name))
  );

drop trigger if exists trg_collections_updated_at on public.collections;
create trigger trg_collections_updated_at
  before update on public.collections
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- notes — editable prose owned by a workspace
-- ---------------------------------------------------------------------------
create table if not exists public.notes (
  id            uuid primary key default gen_random_uuid(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  collection_id uuid references public.collections(id) on delete set null,
  created_by    uuid not null references auth.users(id) on delete cascade,
  title         text not null check (char_length(btrim(title)) between 1 and 500),
  body          text not null default '',
  status        text not null default 'pending'
                  check (status in ('pending', 'processing', 'ready', 'failed', 'archived')),
  processing_stage text check (processing_stage in ('extracting','chunking','embedding','entities')),
  error_message text,
  version       integer not null default 1 check (version >= 1),
  embedding_model text,
  summary       text,
  metadata      jsonb not null default '{}'::jsonb,
  deleted_at    timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint notes_failure_has_message check (status <> 'failed' or error_message is not null)
);

create index if not exists idx_notes_workspace_updated
  on public.notes (workspace_id, updated_at desc)
  where deleted_at is null;

create index if not exists idx_notes_workspace_collection
  on public.notes (workspace_id, collection_id)
  where deleted_at is null;

create index if not exists idx_notes_fts
  on public.notes
  using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, '')));

create index if not exists idx_notes_in_flight
  on public.notes (workspace_id)
  where status in ('pending', 'processing') and deleted_at is null;

drop trigger if exists trg_notes_updated_at on public.notes;
create trigger trg_notes_updated_at
  before update on public.notes
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- documents — uploaded files owned by a workspace
-- ---------------------------------------------------------------------------
create table if not exists public.documents (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  collection_id   uuid references public.collections(id) on delete set null,
  uploaded_by     uuid not null references auth.users(id) on delete cascade,
  title           text not null check (char_length(btrim(title)) between 1 and 500),
  mime_type       text,
  storage_path    text not null,
  file_size       bigint check (file_size is null or file_size >= 0),
  checksum_sha256 text,
  raw_text        text,
  page_count      integer check (page_count is null or page_count >= 0),
  status          text not null default 'pending'
                    check (status in ('pending','processing','ready','failed','archived')),
  processing_stage text check (processing_stage in ('extracting','chunking','embedding','entities')),
  error_message   text,
  version         integer not null default 1 check (version >= 1),
  embedding_model text,
  summary         text,
  metadata        jsonb not null default '{}'::jsonb,
  deleted_at      timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint documents_failure_has_message check (status <> 'failed' or error_message is not null)
);

create index if not exists idx_documents_workspace_updated
  on public.documents (workspace_id, updated_at desc)
  where deleted_at is null;

create index if not exists idx_documents_workspace_collection
  on public.documents (workspace_id, collection_id)
  where deleted_at is null;

-- dedupe within a workspace, not globally: two workspaces may legitimately hold
-- the same file.
create unique index if not exists uq_documents_workspace_checksum
  on public.documents (workspace_id, checksum_sha256)
  where deleted_at is null and checksum_sha256 is not null;

create index if not exists idx_documents_fts
  on public.documents
  using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(raw_text, '')));

create index if not exists idx_documents_in_flight
  on public.documents (workspace_id)
  where status in ('pending', 'processing') and deleted_at is null;

drop trigger if exists trg_documents_updated_at on public.documents;
create trigger trg_documents_updated_at
  before update on public.documents
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- tags — one vocabulary per workspace, shared by notes and documents
-- ---------------------------------------------------------------------------
create table if not exists public.tags (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name         text not null check (char_length(btrim(name)) between 1 and 60),
  created_at   timestamptz not null default now()
);

create unique index if not exists uq_tags_workspace_name
  on public.tags (workspace_id, lower(btrim(name)));

-- ---------------------------------------------------------------------------
-- note_tags / document_tags
-- workspace_id is denormalized on purpose so every policy is the fast,
-- index-backed membership check with no join.
-- ---------------------------------------------------------------------------
create table if not exists public.note_tags (
  note_id      uuid not null references public.notes(id) on delete cascade,
  tag_id       uuid not null references public.tags(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  created_at   timestamptz not null default now(),
  primary key (note_id, tag_id)
);

create index if not exists idx_note_tags_tag on public.note_tags (tag_id);
create index if not exists idx_note_tags_workspace on public.note_tags (workspace_id);

create table if not exists public.document_tags (
  document_id  uuid not null references public.documents(id) on delete cascade,
  tag_id       uuid not null references public.tags(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  created_at   timestamptz not null default now(),
  primary key (document_id, tag_id)
);

create index if not exists idx_document_tags_tag on public.document_tags (tag_id);
create index if not exists idx_document_tags_workspace on public.document_tags (workspace_id);
