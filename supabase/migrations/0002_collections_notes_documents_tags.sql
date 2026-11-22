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
  constraint collections_no_self_parent check (parent_id is null or parent_id <> id),
  constraint collections_id_workspace_key unique (id, workspace_id)
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
  content       text not null default '',
  content_format text not null default 'markdown'
                  check (content_format in ('markdown', 'plain')),
  status        text not null default 'pending'
                  check (status in ('pending', 'processing', 'ready', 'failed', 'archived')),
  processing_stage text check (processing_stage in ('extracting','normalizing','chunking','embedding','entities')),
  error_message text,
  version       integer not null default 1 check (version >= 1),
  embedding_model text,
  summary       text,
  metadata      jsonb not null default '{}'::jsonb,
  deleted_at    timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint notes_failure_has_message check (status <> 'failed' or error_message is not null),
  -- Target for composite foreign keys: lets a child prove it shares the
  -- parent's workspace instead of only pointing at the parent's id.
  constraint notes_id_workspace_key unique (id, workspace_id)
);

create index if not exists idx_notes_workspace_updated
  on public.notes (workspace_id, updated_at desc)
  where deleted_at is null;

create index if not exists idx_notes_workspace_collection
  on public.notes (workspace_id, collection_id)
  where deleted_at is null;

create index if not exists idx_notes_fts
  on public.notes
  using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, '')));

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
  -- filename is what the user called it; file_path is where storage keeps it.
  -- Keeping both means the UI never has to reverse-engineer a path to show a
  -- name, and a re-upload under a different key does not rename the document.
  filename        text not null check (char_length(btrim(filename)) between 1 and 255),
  mime_type       text,
  file_path       text not null,
  file_size       bigint check (file_size is null or file_size >= 0),
  checksum_sha256 text,
  raw_text        text,
  page_count      integer check (page_count is null or page_count >= 0),
  status          text not null default 'pending'
                    check (status in ('pending','processing','ready','failed','archived')),
  processing_stage text check (processing_stage in ('extracting','normalizing','chunking','embedding','entities')),
  error_message   text,
  version         integer not null default 1 check (version >= 1),
  embedding_model text,
  summary         text,
  metadata        jsonb not null default '{}'::jsonb,
  deleted_at      timestamptz,
  -- set when ingest reaches 'ready' or 'failed': the one timestamp an operator
  -- actually wants ("how long did this file take, and did it finish?").
  completed_at    timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint documents_failure_has_message check (status <> 'failed' or error_message is not null),
  constraint documents_id_workspace_key unique (id, workspace_id)
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
  created_at   timestamptz not null default now(),
  constraint tags_id_workspace_key unique (id, workspace_id)
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

-- Workspace consistency, enforced structurally. A plain FK proves the note and
-- the tag exist; a composite FK proves they exist *in the same workspace as this
-- row*. Without these, a service-role script (or a future API bug) could label
-- Alice's note with Bob's tag and only RLS reads would hide the damage.
alter table public.note_tags
  drop constraint if exists note_tags_note_workspace_fkey,
  add constraint note_tags_note_workspace_fkey
    foreign key (note_id, workspace_id) references public.notes (id, workspace_id)
    on delete cascade,
  drop constraint if exists note_tags_tag_workspace_fkey,
  add constraint note_tags_tag_workspace_fkey
    foreign key (tag_id, workspace_id) references public.tags (id, workspace_id)
    on delete cascade;

create table if not exists public.document_tags (
  document_id  uuid not null references public.documents(id) on delete cascade,
  tag_id       uuid not null references public.tags(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  created_at   timestamptz not null default now(),
  primary key (document_id, tag_id)
);

create index if not exists idx_document_tags_tag on public.document_tags (tag_id);
create index if not exists idx_document_tags_workspace on public.document_tags (workspace_id);

alter table public.document_tags
  drop constraint if exists document_tags_document_workspace_fkey,
  add constraint document_tags_document_workspace_fkey
    foreign key (document_id, workspace_id) references public.documents (id, workspace_id)
    on delete cascade,
  drop constraint if exists document_tags_tag_workspace_fkey,
  add constraint document_tags_tag_workspace_fkey
    foreign key (tag_id, workspace_id) references public.tags (id, workspace_id)
    on delete cascade;
