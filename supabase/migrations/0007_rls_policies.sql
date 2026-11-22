-- ============================================================================
-- 0007 — Row Level Security
-- ============================================================================
-- Every user-scoped table gets the same four policies keyed on auth.uid().
-- This migration is kept separate so the security posture is reviewable in a
-- single diff.
--
-- Service-role clients (worker, job enqueueing) bypass RLS by design. Because
-- each policy's WITH CHECK still names user_id = auth.uid(), a mis-scoped
-- worker write executed under a user token is rejected rather than leaked.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Enable RLS everywhere user data lives.
-- ---------------------------------------------------------------------------
alter table public.profiles        enable row level security;
alter table public.collections     enable row level security;
alter table public.documents       enable row level security;
alter table public.tags            enable row level security;
alter table public.document_tags   enable row level security;
alter table public.chunks          enable row level security;
alter table public.entities        enable row level security;
alter table public.chunk_entities  enable row level security;
alter table public.entity_edges    enable row level security;
alter table public.conversations   enable row level security;
alter table public.messages        enable row level security;
alter table public.message_citations enable row level security;
alter table public.jobs            enable row level security;
alter table public.usage_events    enable row level security;

-- ---------------------------------------------------------------------------
-- profiles — read/update own row. No INSERT policy: the auth.users trigger
-- (SECURITY DEFINER) owns creation.
-- ---------------------------------------------------------------------------
drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select using (id = auth.uid());

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update using (id = auth.uid())
  with check (id = auth.uid());

-- ---------------------------------------------------------------------------
-- Standard owner policies. Written out explicitly per table (rather than a
-- DO loop over a list) so each policy is greppable and auditable by name.
-- ---------------------------------------------------------------------------

-- collections
drop policy if exists collections_select_own on public.collections;
create policy collections_select_own on public.collections
  for select using (user_id = auth.uid());
drop policy if exists collections_insert_own on public.collections;
create policy collections_insert_own on public.collections
  for insert with check (user_id = auth.uid());
drop policy if exists collections_update_own on public.collections;
create policy collections_update_own on public.collections
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists collections_delete_own on public.collections;
create policy collections_delete_own on public.collections
  for delete using (user_id = auth.uid());

-- documents
drop policy if exists documents_select_own on public.documents;
create policy documents_select_own on public.documents
  for select using (user_id = auth.uid());
drop policy if exists documents_insert_own on public.documents;
create policy documents_insert_own on public.documents
  for insert with check (user_id = auth.uid());
drop policy if exists documents_update_own on public.documents;
create policy documents_update_own on public.documents
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists documents_delete_own on public.documents;
create policy documents_delete_own on public.documents
  for delete using (user_id = auth.uid());

-- tags
drop policy if exists tags_select_own on public.tags;
create policy tags_select_own on public.tags
  for select using (user_id = auth.uid());
drop policy if exists tags_insert_own on public.tags;
create policy tags_insert_own on public.tags
  for insert with check (user_id = auth.uid());
drop policy if exists tags_update_own on public.tags;
create policy tags_update_own on public.tags
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists tags_delete_own on public.tags;
create policy tags_delete_own on public.tags
  for delete using (user_id = auth.uid());

-- document_tags
drop policy if exists document_tags_select_own on public.document_tags;
create policy document_tags_select_own on public.document_tags
  for select using (user_id = auth.uid());
drop policy if exists document_tags_insert_own on public.document_tags;
create policy document_tags_insert_own on public.document_tags
  for insert with check (user_id = auth.uid());
drop policy if exists document_tags_update_own on public.document_tags;
create policy document_tags_update_own on public.document_tags
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists document_tags_delete_own on public.document_tags;
create policy document_tags_delete_own on public.document_tags
  for delete using (user_id = auth.uid());

-- chunks (written by the worker; read by the owner through the API)
drop policy if exists chunks_select_own on public.chunks;
create policy chunks_select_own on public.chunks
  for select using (user_id = auth.uid());
drop policy if exists chunks_insert_own on public.chunks;
create policy chunks_insert_own on public.chunks
  for insert with check (user_id = auth.uid());
drop policy if exists chunks_update_own on public.chunks;
create policy chunks_update_own on public.chunks
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists chunks_delete_own on public.chunks;
create policy chunks_delete_own on public.chunks
  for delete using (user_id = auth.uid());

-- entities
drop policy if exists entities_select_own on public.entities;
create policy entities_select_own on public.entities
  for select using (user_id = auth.uid());
drop policy if exists entities_insert_own on public.entities;
create policy entities_insert_own on public.entities
  for insert with check (user_id = auth.uid());
drop policy if exists entities_update_own on public.entities;
create policy entities_update_own on public.entities
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists entities_delete_own on public.entities;
create policy entities_delete_own on public.entities
  for delete using (user_id = auth.uid());

-- chunk_entities
drop policy if exists chunk_entities_select_own on public.chunk_entities;
create policy chunk_entities_select_own on public.chunk_entities
  for select using (user_id = auth.uid());
drop policy if exists chunk_entities_insert_own on public.chunk_entities;
create policy chunk_entities_insert_own on public.chunk_entities
  for insert with check (user_id = auth.uid());
drop policy if exists chunk_entities_update_own on public.chunk_entities;
create policy chunk_entities_update_own on public.chunk_entities
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists chunk_entities_delete_own on public.chunk_entities;
create policy chunk_entities_delete_own on public.chunk_entities
  for delete using (user_id = auth.uid());

-- entity_edges
drop policy if exists entity_edges_select_own on public.entity_edges;
create policy entity_edges_select_own on public.entity_edges
  for select using (user_id = auth.uid());
drop policy if exists entity_edges_insert_own on public.entity_edges;
create policy entity_edges_insert_own on public.entity_edges
  for insert with check (user_id = auth.uid());
drop policy if exists entity_edges_update_own on public.entity_edges;
create policy entity_edges_update_own on public.entity_edges
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists entity_edges_delete_own on public.entity_edges;
create policy entity_edges_delete_own on public.entity_edges
  for delete using (user_id = auth.uid());

-- conversations
drop policy if exists conversations_select_own on public.conversations;
create policy conversations_select_own on public.conversations
  for select using (user_id = auth.uid());
drop policy if exists conversations_insert_own on public.conversations;
create policy conversations_insert_own on public.conversations
  for insert with check (user_id = auth.uid());
drop policy if exists conversations_update_own on public.conversations;
create policy conversations_update_own on public.conversations
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists conversations_delete_own on public.conversations;
create policy conversations_delete_own on public.conversations
  for delete using (user_id = auth.uid());

-- messages
drop policy if exists messages_select_own on public.messages;
create policy messages_select_own on public.messages
  for select using (user_id = auth.uid());
drop policy if exists messages_insert_own on public.messages;
create policy messages_insert_own on public.messages
  for insert with check (user_id = auth.uid());
drop policy if exists messages_update_own on public.messages;
create policy messages_update_own on public.messages
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists messages_delete_own on public.messages;
create policy messages_delete_own on public.messages
  for delete using (user_id = auth.uid());

-- message_citations
drop policy if exists message_citations_select_own on public.message_citations;
create policy message_citations_select_own on public.message_citations
  for select using (user_id = auth.uid());
drop policy if exists message_citations_insert_own on public.message_citations;
create policy message_citations_insert_own on public.message_citations
  for insert with check (user_id = auth.uid());
drop policy if exists message_citations_update_own on public.message_citations;
create policy message_citations_update_own on public.message_citations
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists message_citations_delete_own on public.message_citations;
create policy message_citations_delete_own on public.message_citations
  for delete using (user_id = auth.uid());

-- jobs — the user may LOOK at their own queue; all mutation is service-role only.
drop policy if exists jobs_select_own on public.jobs;
create policy jobs_select_own on public.jobs
  for select using (user_id = auth.uid());

-- usage_events — own rows readable; writes are service-role only.
drop policy if exists usage_events_select_own on public.usage_events;
create policy usage_events_select_own on public.usage_events
  for select using (user_id = auth.uid());
