-- ============================================================================
-- 0007 — Row Level Security
-- ============================================================================
-- Tenancy is workspace membership, resolved through the SECURITY DEFINER
-- helpers from 0001. Policies never join directly to workspace_members: the
-- helper is stable, index-backed, and markable as a single InitPlan by the
-- planner, so a policy check costs one lookup rather than one per row.
--
-- Two access levels, mirroring the roles in 0001:
--   SELECT   → is_workspace_member(workspace_id)
--   I/U/D    → can_write_workspace(workspace_id)  (owner | admin | member)
-- management of the workspace itself → can_manage_workspace (owner | admin)
--
-- Service-role clients (worker, enqueueing) bypass RLS by design. Because every
-- WITH CHECK still names the helper, a mis-scoped worker write executed under a
-- user token is rejected rather than leaked.
-- ============================================================================

alter table public.profiles           enable row level security;
alter table public.workspaces         enable row level security;
alter table public.workspace_members  enable row level security;
alter table public.collections        enable row level security;
alter table public.notes              enable row level security;
alter table public.documents          enable row level security;
alter table public.tags               enable row level security;
alter table public.note_tags          enable row level security;
alter table public.document_tags      enable row level security;
alter table public.document_chunks    enable row level security;
alter table public.knowledge_entities enable row level security;
alter table public.chunk_entities     enable row level security;
alter table public.knowledge_links    enable row level security;
alter table public.conversations      enable row level security;
alter table public.chat_messages      enable row level security;
alter table public.message_sources    enable row level security;
alter table public.processing_jobs    enable row level security;
alter table public.usage_events       enable row level security;

-- ---------------------------------------------------------------------------
-- profiles — own row, plus rows of people you share a workspace with (so member
-- lists render names without exposing the whole user table). No INSERT policy:
-- the auth.users trigger owns creation.
-- ---------------------------------------------------------------------------
drop policy if exists profiles_select_own_or_coworker on public.profiles;
create policy profiles_select_own_or_coworker on public.profiles
  for select using (id = auth.uid() or public.shares_workspace(id));

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update using (id = auth.uid()) with check (id = auth.uid());

-- ---------------------------------------------------------------------------
-- workspaces — members can see it; only management can change or delete it.
-- ---------------------------------------------------------------------------
drop policy if exists workspaces_select_member on public.workspaces;
create policy workspaces_select_member on public.workspaces
  for select using (public.is_workspace_member(id));

-- Any signed-in user may create a workspace; they must own it.
drop policy if exists workspaces_insert_self_owned on public.workspaces;
create policy workspaces_insert_self_owned on public.workspaces
  for insert with check (owner_id = auth.uid());

drop policy if exists workspaces_update_manage on public.workspaces;
create policy workspaces_update_manage on public.workspaces
  for update using (public.can_manage_workspace(id))
  with check (public.can_manage_workspace(id));

drop policy if exists workspaces_delete_owner on public.workspaces;
create policy workspaces_delete_owner on public.workspaces
  for delete using (owner_id = auth.uid());

-- ---------------------------------------------------------------------------
-- workspace_members — you can see the roster of workspaces you belong to.
-- Adding and removing members is management-only. Writes are otherwise
-- performed by the service role (invitations) or the signup trigger.
-- ---------------------------------------------------------------------------
drop policy if exists workspace_members_select_member on public.workspace_members;
create policy workspace_members_select_member on public.workspace_members
  for select using (public.is_workspace_member(workspace_id));

drop policy if exists workspace_members_insert_manage on public.workspace_members;
create policy workspace_members_insert_manage on public.workspace_members
  for insert with check (public.can_manage_workspace(workspace_id));

drop policy if exists workspace_members_update_manage on public.workspace_members;
create policy workspace_members_update_manage on public.workspace_members
  for update using (public.can_manage_workspace(workspace_id))
  with check (public.can_manage_workspace(workspace_id));

-- A member may always remove themselves (leaving), or management removes others.
drop policy if exists workspace_members_delete_manage_or_self on public.workspace_members;
create policy workspace_members_delete_manage_or_self on public.workspace_members
  for delete using (
    public.can_manage_workspace(workspace_id)
    or (user_id = auth.uid() and role <> 'owner')
  );

-- ---------------------------------------------------------------------------
-- Standard workspace-scoped tables.
-- Written out per table rather than looped, so every policy is greppable and
-- auditable by name in this one diff.
-- ---------------------------------------------------------------------------

-- collections
drop policy if exists collections_select_member on public.collections;
create policy collections_select_member on public.collections
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists collections_insert_write on public.collections;
create policy collections_insert_write on public.collections
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists collections_update_write on public.collections;
create policy collections_update_write on public.collections
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists collections_delete_write on public.collections;
create policy collections_delete_write on public.collections
  for delete using (public.can_write_workspace(workspace_id));

-- notes
drop policy if exists notes_select_member on public.notes;
create policy notes_select_member on public.notes
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists notes_insert_write on public.notes;
create policy notes_insert_write on public.notes
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists notes_update_write on public.notes;
create policy notes_update_write on public.notes
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists notes_delete_write on public.notes;
create policy notes_delete_write on public.notes
  for delete using (public.can_write_workspace(workspace_id));

-- documents
drop policy if exists documents_select_member on public.documents;
create policy documents_select_member on public.documents
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists documents_insert_write on public.documents;
create policy documents_insert_write on public.documents
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists documents_update_write on public.documents;
create policy documents_update_write on public.documents
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists documents_delete_write on public.documents;
create policy documents_delete_write on public.documents
  for delete using (public.can_write_workspace(workspace_id));

-- tags
drop policy if exists tags_select_member on public.tags;
create policy tags_select_member on public.tags
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists tags_insert_write on public.tags;
create policy tags_insert_write on public.tags
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists tags_update_write on public.tags;
create policy tags_update_write on public.tags
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists tags_delete_write on public.tags;
create policy tags_delete_write on public.tags
  for delete using (public.can_write_workspace(workspace_id));

-- note_tags
drop policy if exists note_tags_select_member on public.note_tags;
create policy note_tags_select_member on public.note_tags
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists note_tags_insert_write on public.note_tags;
create policy note_tags_insert_write on public.note_tags
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists note_tags_update_write on public.note_tags;
create policy note_tags_update_write on public.note_tags
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists note_tags_delete_write on public.note_tags;
create policy note_tags_delete_write on public.note_tags
  for delete using (public.can_write_workspace(workspace_id));

-- document_tags
drop policy if exists document_tags_select_member on public.document_tags;
create policy document_tags_select_member on public.document_tags
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists document_tags_insert_write on public.document_tags;
create policy document_tags_insert_write on public.document_tags
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists document_tags_update_write on public.document_tags;
create policy document_tags_update_write on public.document_tags
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists document_tags_delete_write on public.document_tags;
create policy document_tags_delete_write on public.document_tags
  for delete using (public.can_write_workspace(workspace_id));

-- document_chunks (written by the worker; read by members through the API)
drop policy if exists chunks_select_member on public.document_chunks;
create policy chunks_select_member on public.document_chunks
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists chunks_insert_write on public.document_chunks;
create policy chunks_insert_write on public.document_chunks
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists chunks_update_write on public.document_chunks;
create policy chunks_update_write on public.document_chunks
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists chunks_delete_write on public.document_chunks;
create policy chunks_delete_write on public.document_chunks
  for delete using (public.can_write_workspace(workspace_id));

-- knowledge_entities
drop policy if exists entities_select_member on public.knowledge_entities;
create policy entities_select_member on public.knowledge_entities
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists entities_insert_write on public.knowledge_entities;
create policy entities_insert_write on public.knowledge_entities
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists entities_update_write on public.knowledge_entities;
create policy entities_update_write on public.knowledge_entities
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists entities_delete_write on public.knowledge_entities;
create policy entities_delete_write on public.knowledge_entities
  for delete using (public.can_write_workspace(workspace_id));

-- chunk_entities
drop policy if exists chunk_entities_select_member on public.chunk_entities;
create policy chunk_entities_select_member on public.chunk_entities
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists chunk_entities_insert_write on public.chunk_entities;
create policy chunk_entities_insert_write on public.chunk_entities
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists chunk_entities_update_write on public.chunk_entities;
create policy chunk_entities_update_write on public.chunk_entities
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists chunk_entities_delete_write on public.chunk_entities;
create policy chunk_entities_delete_write on public.chunk_entities
  for delete using (public.can_write_workspace(workspace_id));

-- knowledge_links
drop policy if exists knowledge_links_select_member on public.knowledge_links;
create policy knowledge_links_select_member on public.knowledge_links
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists knowledge_links_insert_write on public.knowledge_links;
create policy knowledge_links_insert_write on public.knowledge_links
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists knowledge_links_update_write on public.knowledge_links;
create policy knowledge_links_update_write on public.knowledge_links
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists knowledge_links_delete_write on public.knowledge_links;
create policy knowledge_links_delete_write on public.knowledge_links
  for delete using (public.can_write_workspace(workspace_id));

-- conversations
drop policy if exists conversations_select_member on public.conversations;
create policy conversations_select_member on public.conversations
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists conversations_insert_write on public.conversations;
create policy conversations_insert_write on public.conversations
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists conversations_update_write on public.conversations;
create policy conversations_update_write on public.conversations
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists conversations_delete_write on public.conversations;
create policy conversations_delete_write on public.conversations
  for delete using (public.can_write_workspace(workspace_id));

-- chat_messages
drop policy if exists chat_messages_select_member on public.chat_messages;
create policy chat_messages_select_member on public.chat_messages
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists chat_messages_insert_write on public.chat_messages;
create policy chat_messages_insert_write on public.chat_messages
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists chat_messages_update_write on public.chat_messages;
create policy chat_messages_update_write on public.chat_messages
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists chat_messages_delete_write on public.chat_messages;
create policy chat_messages_delete_write on public.chat_messages
  for delete using (public.can_write_workspace(workspace_id));

-- message_sources
drop policy if exists message_sources_select_member on public.message_sources;
create policy message_sources_select_member on public.message_sources
  for select using (public.is_workspace_member(workspace_id));
drop policy if exists message_sources_insert_write on public.message_sources;
create policy message_sources_insert_write on public.message_sources
  for insert with check (public.can_write_workspace(workspace_id));
drop policy if exists message_sources_update_write on public.message_sources;
create policy message_sources_update_write on public.message_sources
  for update using (public.can_write_workspace(workspace_id))
  with check (public.can_write_workspace(workspace_id));
drop policy if exists message_sources_delete_write on public.message_sources;
create policy message_sources_delete_write on public.message_sources
  for delete using (public.can_write_workspace(workspace_id));

-- processing_jobs — members may LOOK at the queue; all mutation is service-role.
drop policy if exists jobs_select_member on public.processing_jobs;
create policy jobs_select_member on public.processing_jobs
  for select using (public.is_workspace_member(workspace_id));

-- usage_events — readable by workspace management; written by the service role.
drop policy if exists usage_events_select_manage on public.usage_events;
create policy usage_events_select_manage on public.usage_events
  for select using (public.can_manage_workspace(workspace_id));
