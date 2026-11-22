-- ============================================================================
-- 0008 — hybrid_search: the retrieval primitive
-- ============================================================================
-- One round trip. One filtering rule. Two candidate arms fused by Reciprocal
-- Rank Fusion (RAG-PIPELINE.md §3.2).
--
--   vector arm  : HNSW cosine over document_chunks.embedding      → paraphrase
--   keyword arm : ts_rank_cd over document_chunks.content tsvector → exact terms
--
-- Because chunks now have two possible parents, the function resolves the
-- title and parent id in a single LEFT JOIN pair and reports which kind the hit
-- came from. Consumers (chat, search, citations) therefore never need to guess.
--
-- fused_score is a raw RRF value (order ~1/60). It is a ranking signal, not a
-- probability, and it can NEVER express irrelevance: HNSW returns k neighbours
-- for any query vector, so a top-1 fused score is ~constant no matter how
-- unrelated the query was.
--
-- The grounded/ungrounded decision therefore keys on p_min_similarity (absolute
-- cosine similarity, applied inside the vector arm), not on fused_score.
--
-- Security: SECURITY INVOKER (the default). Callers pass p_workspace_id
-- explicitly AND remain subject to RLS — the tenant filter is applied twice, by
-- two independent mechanisms.
-- ============================================================================

create or replace function public.hybrid_search(
  p_workspace_id    uuid,
  p_query           text,
  p_embedding       vector(1536) default null,
  p_match_count     integer default 8,
  p_candidate_count integer default 40,
  p_vector_weight   double precision default 1.0,
  p_keyword_weight  double precision default 0.7,
  p_rrf_k           integer default 60,
  p_collection_id   uuid default null,
  p_created_after   timestamptz default null,
  p_created_before  timestamptz default null,
  p_min_score       double precision default 0.0,
  p_min_similarity  double precision default 0.0,
  p_source_ids      uuid[] default null,
  p_kind            text default null
)
returns table (
  chunk_id        uuid,
  source_kind     text,
  note_id         uuid,
  document_id     uuid,
  source_title    text,
  content         text,
  heading_path    text,
  page_start      integer,
  page_end        integer,
  char_start      integer,
  char_end        integer,
  vector_rank     integer,
  keyword_rank    integer,
  vector_distance double precision,
  vector_similarity double precision,
  keyword_score   double precision,
  fused_score     double precision
)
language sql
stable
as $$
with params as (
  select
    case when nullif(btrim(p_query), '') is null then null
         else websearch_to_tsquery('english', p_query)
    end as tsq
),
-- Every filter is applied once, here, so both arms see the same candidate set.
filtered as (
  select
    c.id                       as cid,
    case when c.note_id is not null then 'note' else 'document' end as kind,
    c.note_id                  as nid,
    c.document_id              as did,
    c.content                  as body,
    c.heading_path             as hpath,
    c.page_start               as pstart,
    c.page_end                 as pend,
    c.char_start               as cstart,
    c.char_end                 as cend,
    c.embedding                as emb,
    coalesce(n.title, d.title) as stitle,
    coalesce(n.collection_id, d.collection_id) as coll,
    coalesce(n.created_at, d.created_at)       as created
  from public.document_chunks c
  left join public.notes n
    on n.id = c.note_id and n.deleted_at is null
  left join public.documents d
    on d.id = c.document_id and d.deleted_at is null
  where c.workspace_id = p_workspace_id
    -- a chunk whose parent was soft-deleted must not surface
    and (n.id is not null or d.id is not null)
    and (p_collection_id is null or coalesce(n.collection_id, d.collection_id) = p_collection_id)
    and (p_created_after is null or coalesce(n.created_at, d.created_at) >= p_created_after)
    and (p_created_before is null or coalesce(n.created_at, d.created_at) <= p_created_before)
    -- kind and id filters, so a caller can scope to notes or to particular sources
    and (p_kind is null or (case when c.note_id is not null then 'note' else 'document' end) = p_kind)
    and (
      p_source_ids is null
      or coalesce(c.note_id, c.document_id) = any(p_source_ids)
    )
),
-- ---------------------------------------------------------------------------
-- Arm 1: vector candidates (skipped when no embedding is supplied, which is how
-- keyword-only mode is expressed).
-- ---------------------------------------------------------------------------
vector_candidates as (
  select
    f.cid,
    f.emb <=> p_embedding as distance,
    row_number() over (order by f.emb <=> p_embedding) as rnk
  from filtered f
  where p_embedding is not null
    and f.emb is not null
    -- ABSOLUTE relevance gate.
    -- HNSW returns k neighbours for ANY query vector, including nonsense, so
    -- rank alone can never express "this is unrelated". Cosine similarity can.
    and (1 - (f.emb <=> p_embedding)) >= p_min_similarity
  order by f.emb <=> p_embedding
  limit greatest(p_candidate_count, 1)
),
-- ---------------------------------------------------------------------------
-- Arm 2: keyword candidates
-- ---------------------------------------------------------------------------
keyword_candidates as (
  select
    f.cid,
    ts_rank_cd(to_tsvector('english', f.body), prm.tsq) as score,
    row_number() over (
      order by ts_rank_cd(to_tsvector('english', f.body), prm.tsq) desc
    ) as rnk
  from filtered f
  cross join params prm
  where prm.tsq is not null
    and to_tsvector('english', f.body) @@ prm.tsq
  order by score desc
  limit greatest(p_candidate_count, 1)
),
-- ---------------------------------------------------------------------------
-- Fusion (Reciprocal Rank Fusion)
-- ---------------------------------------------------------------------------
fused as (
  select
    coalesce(v.cid, k.cid)            as cid,
    v.rnk                             as vrank,
    k.rnk                             as krank,
    v.distance                        as vdist,
    case when v.distance is null then null else 1 - v.distance end as vsim,
    k.score                           as kscore,
    coalesce(p_vector_weight  / (p_rrf_k + v.rnk), 0.0)
    + coalesce(p_keyword_weight / (p_rrf_k + k.rnk), 0.0) as score
  from vector_candidates v
  full outer join keyword_candidates k on v.cid = k.cid
)
select
  fz.cid,
  f.kind,
  f.nid,
  f.did,
  f.stitle,
  f.body,
  f.hpath,
  f.pstart,
  f.pend,
  f.cstart,
  f.cend,
  fz.vrank::integer,
  fz.krank::integer,
  fz.vdist,
  fz.vsim,
  fz.kscore,
  fz.score
from fused fz
join filtered f on f.cid = fz.cid
where fz.score >= p_min_score
order by fz.score desc, fz.vdist asc nulls last, f.cid
limit greatest(p_match_count, 1);
$$;

comment on function public.hybrid_search(
  uuid, text, vector, integer, integer, double precision, double precision,
  integer, uuid, timestamptz, timestamptz, double precision, double precision,
  uuid[], text
) is
  'Hybrid retrieval (vector + keyword, RRF-fused, similarity-gated) over notes and documents. Returns chunk-level hits with both arm ranks and the source kind so the UI can explain why a result surfaced.';
