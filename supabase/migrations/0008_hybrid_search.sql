-- ============================================================================
-- 0008 — hybrid_search: the retrieval primitive
-- ============================================================================
-- One round trip. One filtering rule. Two candidate arms fused by Reciprocal
-- Rank Fusion (RAG-PIPELINE.md §3.2).
--
--   vector arm  : HNSW cosine over chunks.embedding      → semantic recall
--   keyword arm : ts_rank_cd over chunks.content tsvector → exact identifiers
--
-- RRF is used because cosine distance and ts_rank_cd live on incomparable
-- scales; rank-based fusion needs no calibration and stays stable as the
-- corpus grows.
--
-- Security: SECURITY INVOKER (the default). Callers pass p_user_id explicitly
-- AND remain subject to RLS, which is the intent — the tenant filter is applied
-- twice, by two independent mechanisms.
--
-- fused_score is a raw RRF value (order ~1/60). It is a ranking signal, not a
-- probability, and it can NEVER express irrelevance: HNSW returns k neighbours
-- for any query vector, so a top-1 fused score is ~constant no matter how
-- unrelated the query was.
--
-- The grounded/ungrounded decision therefore keys on p_min_similarity (absolute
-- cosine similarity, applied inside the vector arm), not on fused_score.
-- vector_similarity is returned so the caller can inspect the actual evidence.
-- ============================================================================

create or replace function public.hybrid_search(
  p_user_id        uuid,
  p_query          text,
  p_embedding      vector(1536) default null,
  p_match_count    integer default 8,
  p_candidate_count integer default 40,
  p_vector_weight  double precision default 1.0,
  p_keyword_weight double precision default 0.7,
  p_rrf_k          integer default 60,
  p_collection_id  uuid default null,
  p_document_ids   uuid[] default null,
  p_created_after  timestamptz default null,
  p_created_before timestamptz default null,
  p_min_score      double precision default 0.0,
  p_min_similarity double precision default 0.0
)
returns table (
  chunk_id        uuid,
  document_id     uuid,
  document_title  text,
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
    c.id            as cid,
    c.document_id   as did,
    c.content       as body,
    c.heading_path  as hpath,
    c.page_start    as pstart,
    c.page_end      as pend,
    c.char_start    as cstart,
    c.char_end      as cend,
    c.embedding     as emb,
    d.title         as dtitle
  from public.chunks c
  join public.documents d on d.id = c.document_id
  where c.user_id = p_user_id
    and d.deleted_at is null
    and (p_collection_id is null or d.collection_id = p_collection_id)
    and (p_document_ids  is null or d.id = any(p_document_ids))
    and (p_created_after is null or d.created_at >= p_created_after)
    and (p_created_before is null or d.created_at <= p_created_before)
),
-- ---------------------------------------------------------------------------
-- Arm 1: vector candidates (skipped entirely when no embedding is supplied,
-- which is how keyword-only mode is expressed).
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
  f.did,
  f.dtitle,
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
order by fz.score desc, fz.vdist asc nulls last, f.did, f.cstart
limit greatest(p_match_count, 1);
$$;

comment on function public.hybrid_search(uuid, text, vector, integer, integer, double precision, double precision, integer, uuid, uuid[], timestamptz, timestamptz, double precision, double precision) is
  'Hybrid retrieval (vector + keyword, RRF-fused, similarity-gated). Returns chunk-level hits with both arm ranks so the UI can explain why a result surfaced.';

-- ---------------------------------------------------------------------------
-- Entity helper: recompute a mention counter after worker writes.
-- Kept in SQL so the graph never drifts from chunk_entities.
-- ---------------------------------------------------------------------------
create or replace function public.refresh_entity_mentions(p_entity_id uuid)
returns void
language sql
security definer
set search_path = public
as $$
  update public.entities e
  set mention_count = sub.cnt,
      last_seen_at  = now()
  from (
    select count(*)::integer as cnt
    from public.chunk_entities ce
    where ce.entity_id = p_entity_id
  ) sub
  where e.id = p_entity_id;
$$;
