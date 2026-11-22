-- ============================================================================
-- 0005 — conversations, messages, citations
-- ============================================================================

create table if not exists public.conversations (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  title       text,
  archived_at timestamptz,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists idx_conversations_user_updated
  on public.conversations (user_id, updated_at desc)
  where archived_at is null;

drop trigger if exists trg_conversations_updated_at on public.conversations;
create trigger trg_conversations_updated_at
  before update on public.conversations
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- messages
-- ---------------------------------------------------------------------------
create table if not exists public.messages (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references auth.users(id) on delete cascade,
  conversation_id  uuid not null references public.conversations(id) on delete cascade,
  role             text not null check (role in ('user','assistant','system')),
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

create index if not exists idx_messages_conversation_created
  on public.messages (conversation_id, created_at);

create index if not exists idx_messages_user on public.messages (user_id);

-- ---------------------------------------------------------------------------
-- message_citations — the proof, persisted per answer
-- chunk_id cascades: if the source chunk disappears the citation cannot
-- silently point at nothing. document_title is denormalized so a historical
-- answer still renders its source label after a rename.
-- ---------------------------------------------------------------------------
create table if not exists public.message_citations (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  message_id     uuid not null references public.messages(id) on delete cascade,
  chunk_id       uuid not null references public.chunks(id) on delete cascade,
  document_id    uuid not null references public.documents(id) on delete cascade,
  document_title text not null,
  marker         integer not null check (marker >= 1),
  quote          text,
  page           integer check (page is null or page >= 1),
  heading_path   text,
  score          numeric(5,4) check (score is null or (score >= 0 and score <= 1)),
  created_at     timestamptz not null default now(),

  constraint uq_message_citation_marker unique (message_id, marker)
);

create index if not exists idx_message_citations_message
  on public.message_citations (message_id);
create index if not exists idx_message_citations_chunk
  on public.message_citations (chunk_id);
