-- ============================================================================
-- 0005 — conversations, chat_messages, message_sources
-- ============================================================================
-- Conversations belong to a workspace, not a user: a shared workspace shares
-- its research trail. created_by records who asked.
-- ============================================================================

create table if not exists public.conversations (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  created_by   uuid not null references auth.users(id) on delete cascade,
  title        text,
  archived_at  timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index if not exists idx_conversations_workspace_updated
  on public.conversations (workspace_id, updated_at desc)
  where archived_at is null;

drop trigger if exists trg_conversations_updated_at on public.conversations;
create trigger trg_conversations_updated_at
  before update on public.conversations
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- chat_messages
-- ---------------------------------------------------------------------------
create table if not exists public.chat_messages (
  id               uuid primary key default gen_random_uuid(),
  workspace_id     uuid not null references public.workspaces(id) on delete cascade,
  conversation_id  uuid not null references public.conversations(id) on delete cascade,
  role             text not null check (role in ('user', 'assistant', 'system')),
  content          text not null default '',
  grounded         boolean,
  retrieval_debug  jsonb,
  model            text,
  tokens_in        integer check (tokens_in is null or tokens_in >= 0),
  tokens_out       integer check (tokens_out is null or tokens_out >= 0),
  latency_ms       integer check (latency_ms is null or latency_ms >= 0),
  helpful          boolean,
  feedback_reason  text,
  created_at       timestamptz not null default now()
);

create index if not exists idx_chat_messages_conversation_created
  on public.chat_messages (conversation_id, created_at);

create index if not exists idx_chat_messages_workspace on public.chat_messages (workspace_id);

-- ---------------------------------------------------------------------------
-- message_sources — the proof, persisted per answer
-- chunk_id cascades with its chunk: if the passage disappears the citation
-- cannot silently point at nothing. source_title is denormalized so a historical
-- answer still renders its label after a rename. source_kind states which parent
-- the chunk came from — the one place the split-table model surfaces in the
-- citation path, resolved once at write time rather than on every read.
-- ---------------------------------------------------------------------------
create table if not exists public.message_sources (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  message_id   uuid not null references public.chat_messages(id) on delete cascade,
  chunk_id     uuid not null references public.document_chunks(id) on delete cascade,
  source_kind  text not null check (source_kind in ('note', 'document')),
  note_id      uuid references public.notes(id) on delete cascade,
  document_id  uuid references public.documents(id) on delete cascade,
  source_title text not null,
  marker       integer not null check (marker >= 1),
  quote        text,
  page         integer check (page is null or page >= 1),
  heading_path text,
  score        numeric(5,4) check (score is null or (score >= 0 and score <= 1)),
  created_at   timestamptz not null default now(),

  constraint uq_message_source_marker unique (message_id, marker),
  -- the kind must agree with which parent id is present
  constraint message_sources_kind_matches_parent check (
    (source_kind = 'note' and note_id is not null and document_id is null)
    or (source_kind = 'document' and document_id is not null and note_id is null)
  )
);

create index if not exists idx_message_sources_message on public.message_sources (message_id);
create index if not exists idx_message_sources_chunk on public.message_sources (chunk_id);
