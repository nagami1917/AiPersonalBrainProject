-- ============================================================================
-- 0004 — knowledge graph: knowledge_entities, chunk mentions, knowledge_links
-- ============================================================================
-- A derived view over chunks, never a second source of truth. Rebuilding the
-- graph from chunks must always be possible.
-- ============================================================================

create table if not exists public.knowledge_entities (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  name            text not null check (char_length(btrim(name)) between 1 and 200),
  normalized_name text not null check (char_length(btrim(normalized_name)) between 1 and 200),
  entity_type     text not null default 'other'
                    check (entity_type in ('person','organization','project','concept','place','event','other')),
  description     text,
  first_seen_at   timestamptz not null default now(),
  last_seen_at    timestamptz not null default now(),
  mention_count   integer not null default 0 check (mention_count >= 0),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create unique index if not exists uq_entities_workspace_normalized
  on public.knowledge_entities (workspace_id, normalized_name);

create index if not exists idx_entities_workspace_type
  on public.knowledge_entities (workspace_id, entity_type);

-- Target for the composite FKs below.
alter table public.knowledge_entities
  add constraint knowledge_entities_id_workspace_key unique (id, workspace_id);

drop trigger if exists trg_entities_updated_at on public.knowledge_entities;
create trigger trg_entities_updated_at
  before update on public.knowledge_entities
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- chunk_entities — which entity is mentioned where (the citation trail)
-- ---------------------------------------------------------------------------
create table if not exists public.chunk_entities (
  chunk_id     uuid not null references public.document_chunks(id) on delete cascade,
  entity_id    uuid not null references public.knowledge_entities(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  confidence   numeric(3,2) not null default 0.50 check (confidence >= 0 and confidence <= 1),
  created_at   timestamptz not null default now(),
  primary key (chunk_id, entity_id)
);

create index if not exists idx_chunk_entities_entity on public.chunk_entities (entity_id);
create index if not exists idx_chunk_entities_workspace on public.chunk_entities (workspace_id);

-- A mention links a chunk and an entity that must both live in this workspace.
-- Chunks are keyed on (id, workspace_id) in 0003; entities get their key above.
alter table public.chunk_entities
  drop constraint if exists chunk_entities_chunk_workspace_fkey,
  add constraint chunk_entities_chunk_workspace_fkey
    foreign key (chunk_id, workspace_id) references public.document_chunks (id, workspace_id)
    on delete cascade,
  drop constraint if exists chunk_entities_entity_workspace_fkey,
  add constraint chunk_entities_entity_workspace_fkey
    foreign key (entity_id, workspace_id) references public.knowledge_entities (id, workspace_id)
    on delete cascade;

-- ---------------------------------------------------------------------------
-- knowledge_links — directed relations between entities
-- ---------------------------------------------------------------------------
create table if not exists public.knowledge_links (
  id               uuid primary key default gen_random_uuid(),
  workspace_id     uuid not null references public.workspaces(id) on delete cascade,
  source_id        uuid not null references public.knowledge_entities(id) on delete cascade,
  target_id        uuid not null references public.knowledge_entities(id) on delete cascade,
  relation         text not null default 'co_occurs'
                     check (char_length(btrim(relation)) between 1 and 80),
  weight           integer not null default 1 check (weight > 0),
  created_at       timestamptz not null default now(),
  constraint knowledge_links_no_self_loop check (source_id <> target_id)
);

create unique index if not exists uq_knowledge_links_triple
  on public.knowledge_links (workspace_id, source_id, target_id, relation);

create index if not exists idx_knowledge_links_source on public.knowledge_links (source_id);
create index if not exists idx_knowledge_links_target on public.knowledge_links (target_id);

-- Both ends must be entities in the same workspace as the edge. Edges are the
-- easiest place for a graph bug to leak data across tenants, because they join
-- two rows rather than pointing at one.
alter table public.knowledge_links
  drop constraint if exists knowledge_links_source_workspace_fkey,
  add constraint knowledge_links_source_workspace_fkey
    foreign key (source_id, workspace_id) references public.knowledge_entities (id, workspace_id)
    on delete cascade,
  drop constraint if exists knowledge_links_target_workspace_fkey,
  add constraint knowledge_links_target_workspace_fkey
    foreign key (target_id, workspace_id) references public.knowledge_entities (id, workspace_id)
    on delete cascade;

-- ---------------------------------------------------------------------------
-- Mention counter recomputation, kept in SQL so the graph cannot drift.
-- ---------------------------------------------------------------------------
create or replace function public.refresh_entity_mentions(p_entity_id uuid)
returns void
language sql
security definer
set search_path = public, pg_temp
as $$
  update public.knowledge_entities e
  set mention_count = sub.cnt,
      last_seen_at  = now()
  from (
    select count(*)::integer as cnt
    from public.chunk_entities ce
    where ce.entity_id = p_entity_id
  ) sub
  where e.id = p_entity_id;
$$;
