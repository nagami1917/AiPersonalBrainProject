-- ============================================================================
-- 0017 — hybrid_search(): the tag filter that the API already advertised
-- ============================================================================
-- `SearchFilters.tag` has been in the request schema since chat/search shipped.
-- It was never in this function's signature and never read by the Python
-- translation layer, so a request carrying `"filters": {"tag": "finance"}`
-- returned results from every tag. A filter that is accepted, echoed back in no
-- error, and then ignored is worse than one that is rejected: the caller has no
-- way to tell "nothing matched" from "the filter did nothing".
--
-- ## Why the filter is a set membership rather than a join
--
-- A chunk is filtered by its *parent's* tags, and a parent may carry several.
-- Joining note_tags/document_tags here would multiply chunk rows by tag count
-- and inflate both arms' candidate counts (a note with 3 matching tags would
-- occupy 3 of the 40 candidate slots). `exists` keeps it a predicate: one row
-- per chunk, and the planner can stop at the first matching tag.
--
-- ## Why the tag is resolved to an id first
--
-- `tags` has a unique index on `(workspace_id, lower(btrim(name)))`, so a
-- case-insensitive lookup by name is a single index probe. Doing the comparison
-- inside the EXISTS would repeat it per chunk and would also let a workspace
-- pass a tag name that belongs to another workspace — the lookup is scoped by
-- `p_workspace_id`, so a foreign tag resolves to no id and correctly matches
-- nothing rather than matching everything.
--
-- ## Signature change and why CREATE OR REPLACE is not enough
--
-- Adding a parameter changes the function's identity. `create or replace` with
-- a new trailing default parameter would leave the OLD 15-argument function in
-- place alongside the new one, and PostgREST's named-argument resolution would
-- then be ambiguous. The old signature is dropped explicitly.
-- ============================================================================

drop function if exists public.hybrid_search(
  uuid, text, vector, integer, integer, double precision, double precision,
  integer, uuid, timestamptz, timestamptz, double precision, double precision,
  uuid[], text
);

create or replace function public.hybrid_search(
  p_workspace_id    uuid,
  p_query           text,
  p_embedding       vector(1536)      default null,
  p_match_count     integer           default 8,
  p_candidate_count integer           default 40,
  p_vector_weight   double precision  default 1.0,
  p_keyword_weight  double precision  default 0.7,
  p_rrf_k           integer           default 60,
  p_collection_id   uuid              default null,
  p_created_after   timestamptz       default null,
  p_created_before  timestamptz       default null,
  p_min_score       double precision  default 0.0,
  p_min_similarity  double precision  default 0.0,
  p_source_ids      uuid[]            default null,
  p_kind            text              default null,
  p_tag             text              default null
)
returns table (
  chunk_id         uuid,
  source_kind      text,
  note_id          uuid,
  document_id      uuid,
  source_title     text,
  source_filename  text,
  content          text,
  heading_path     text,
  page_number      integer,
  page_end         integer,
  char_start       integer,
  char_end         integer,
  vector_rank      integer,
  keyword_rank     integer,
  vector_distance  double precision,
  vector_similarity double precision,
  keyword_score    double precision,
  fused_score      double precision
)
language sql
stable
as $$
with params as (
  select
    case when nullif(btrim(p_query), '') is null then null
         else websearch_to_tsquery('english', p_query)
    end as tsq,
    -- Resolved once, not per chunk. Null means either no filter was asked for
    -- or the name does not exist in this workspace; the two are distinguished
    -- by p_tag itself below, so a typo'd tag returns zero rows instead of all
    -- rows.
    (
      select t.id from public.tags t
       where t.workspace_id = p_workspace_id
         and lower(btrim(t.name)) = lower(btrim(p_tag))
       limit 1
    ) as tag_id
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
    c.page_number              as pstart,
    c.page_end                 as pend,
    c.char_start               as cstart,
    c.char_end                 as cend,
    c.embedding                as emb,
    coalesce(n.title, d.title) as stitle,
    -- the spec's citation shape names the uploaded file; notes have none, so the
    -- title doubles as the display name there
    coalesce(n.title, d.filename, d.title) as sfilename,
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
    -- ...and neither must one whose parent is still mid-pipeline. Design §4.1
    -- marks a source complete only after summary and entity extraction, so
    -- 'pending'/'processing' rows are partial work: serving them would present a
    -- half-indexed document as a citable source. Chunks are written before the
    -- status flips, so this is the gate that keeps that ordering honest.
    and coalesce(n.status, d.status) = 'ready'
    and (p_collection_id is null or coalesce(n.collection_id, d.collection_id) = p_collection_id)
    and (p_created_after is null or coalesce(n.created_at, d.created_at) >= p_created_after)
    and (p_created_before is null or coalesce(n.created_at, d.created_at) <= p_created_before)
    -- kind and id filters, so a caller can scope to notes or to particular sources
    and (p_kind is null or (case when c.note_id is not null then 'note' else 'document' end) = p_kind)
    and (
      p_source_ids is null
      or coalesce(c.note_id, c.document_id) = any(p_source_ids)
    )
    -- Tag filter. `p_tag is null` = not asked. A non-null p_tag that resolves to
    -- no tag_id matches nothing, which is the correct answer for an unknown tag.
    and (
      p_tag is null
      or exists (
        select 1
          from params prm
         where prm.tag_id is not null
           and (
             exists (
               select 1 from public.note_tags nt
                where nt.note_id = c.note_id and nt.tag_id = prm.tag_id
             )
             or exists (
               select 1 from public.document_tags dt
                where dt.document_id = c.document_id and dt.tag_id = prm.tag_id
             )
           )
      )
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
    -- A zero weight means "this arm is not part of this query", not "score its
    -- rows zero". Without this the arm still returns ranked rows at 0.0, which
    -- full-outer-joins into the fused set: a semantic-only search would come back
    -- full of keyword matches it was explicitly asked to exclude.
    and p_vector_weight > 0
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
    and p_keyword_weight > 0
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
  f.sfilename,
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

comment on function public.hybrid_search is
  'Hybrid (vector + keyword) chunk retrieval, workspace-scoped. p_tag filters by the parent''s tag name, case-insensitively; an unknown tag matches nothing.';

-- 0011 granted execute before this signature existed; the recreated function
-- needs the grant again or every caller loses EXECUTE.
grant execute on function public.hybrid_search(
  uuid, text, vector, integer, integer, double precision, double precision,
  integer, uuid, timestamptz, timestamptz, double precision, double precision,
  uuid[], text, text
) to authenticated, service_role;
