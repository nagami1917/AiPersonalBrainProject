-- ============================================================================
-- 0004 — knowledge graph: entities, mentions, edges
-- ============================================================================
-- The graph is a derived view over chunks, never a second source of truth.
-- ============================================================================

create table if not exists public.entities (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
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

create unique index if not exists uq_entities_user_normalized
  on public.entities (user_id, normalized_name);

create index if not exists idx_entities_user_type
  on public.entities (user_id, entity_type);

drop trigger if exists trg_entities_updated_at on public.entities;
create trigger trg_entities_updated_at
  before update on public.entities
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- chunk_entities — which entity is mentioned where (the citation trail)
-- ---------------------------------------------------------------------------
create table if not exists public.chunk_entities (
  chunk_id   uuid not null references public.chunks(id) on delete cascade,
  entity_id  uuid not null references public.entities(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  confidence numeric(3,2) not null default 0.50 check (confidence >= 0 and confidence <= 1),
  created_at timestamptz not null default now(),
  primary key (chunk_id, entity_id)
);

create index if not exists idx_chunk_entities_entity on public.chunk_entities (entity_id);
create index if not exists idx_chunk_entities_user on public.chunk_entities (user_id);

-- ---------------------------------------------------------------------------
-- entity_edges — co-occurrence relations
-- ---------------------------------------------------------------------------
create table if not exists public.entity_edges (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references auth.users(id) on delete cascade,
  source_entity_id uuid not null references public.entities(id) on delete cascade,
  target_entity_id uuid not null references public.entities(id) on delete cascade,
  relation         text not null default 'co_occurs' check (char_length(btrim(relation)) between 1 and 80),
  weight           integer not null default 1 check (weight > 0),
  created_at       timestamptz not null default now(),
  constraint entity_edges_no_self_loop check (source_entity_id <> target_entity_id)
);

create unique index if not exists uq_entity_edges_triple
  on public.entity_edges (user_id, source_entity_id, target_entity_id, relation);

create index if not exists idx_entity_edges_source on public.entity_edges (source_entity_id);
create index if not exists idx_entity_edges_target on public.entity_edges (target_entity_id);
