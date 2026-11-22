"""Suggested connections: pairs of notes that are related but not yet linked.

This is not "more like this" search. The question is narrower — *what should I
connect that I have not connected* — so anything already linked, in either
direction, is excluded before ranking. A suggestion panel that proposes edges
the graph already has is noise the user learns to ignore.

Ranking is shared-tag count first and embedding proximity second, because a
shared tag is a claim the user made deliberately and a vector is a guess. Notes
without an embedding still get suggested on tags alone rather than dropping out.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.deps import UserConn, WorkspaceCtx

router = APIRouter(prefix="/workspaces/{workspace_id}/suggestions", tags=["suggestions"])


# A note's centroid is the mean of its chunk embeddings. Mean-of-chunks rather
# than a stored per-note vector because chunks are re-embedded whenever content
# changes, so the centroid is never stale.
_CENTROIDS = """
select c.note_id as note_id, avg(c.embedding) as centroid
from public.document_chunks c
where c.workspace_id = $1 and c.note_id is not null
group by c.note_id
having count(c.embedding) filter (where c.embedding is not null) > 0
"""


@router.get("", response_model=list[dict[str, Any]])
async def suggest_connections(
    ctx: WorkspaceCtx,
    conn: UserConn,
    note_id: str | None = Query(
        default=None, description="Anchor note; omit to survey the workspace"
    ),
    limit: int = Query(default=5, ge=1, le=20),
) -> list[dict[str, Any]]:
    """Unlinked pairs, ranked by shared tags then embedding proximity.

    With `note_id`, the anchor's own suggestions. Without it, the strongest
    pairs anywhere in the workspace — which is what a Welcome panel wants.
    """
    rows = await conn.fetch(
        f"""
        with live as (
          select id, title from public.notes
          where workspace_id = $1 and deleted_at is null
        ),
        tagged as (
          select nt.note_id, array_agg(t.name order by t.name) as tags
          from public.note_tags nt
          join public.tags t on t.id = nt.tag_id
          where nt.workspace_id = $1
          group by nt.note_id
        ),
        paired as (
          -- Order by title, not by id. UUID order and title order are
          -- independent, so constraining on *both* silently drops every pair
          -- whose two orderings disagree — half of all pairs, in practice.
          select la.id as a_id, lb.id as b_id,
                 array(select unnest(ta.tags) intersect select unnest(tb.tags)) as shared_tags
          from live la
          join live lb on la.title < lb.title
          join tagged ta on ta.note_id = la.id
          join tagged tb on tb.note_id = lb.id
          where cardinality(
                  array(select unnest(ta.tags) intersect select unnest(tb.tags))) > 0
        ),
        centroids as ({_CENTROIDS})
        select p.a_id, p.b_id, p.shared_tags,
               na.title as a_title, nb.title as b_title,
               case
                 when ca.centroid is null or cb.centroid is null then null
                 else round((1 - (ca.centroid <=> cb.centroid))::numeric, 4)
               end as similarity
        from paired p
        join live na on na.id = p.a_id
        join live nb on nb.id = p.b_id
        left join centroids ca on ca.note_id = p.a_id
        left join centroids cb on cb.note_id = p.b_id
        where ($2::uuid is null or p.a_id = $2 or p.b_id = $2)
          -- anything already linked, in either direction, is not a suggestion
          and not exists (
                select 1 from public.note_links nl
                where nl.workspace_id = $1
                  and nl.target_note_id is not null
                  and ((nl.source_note_id = p.a_id and nl.target_note_id = p.b_id)
                    or (nl.source_note_id = p.b_id and nl.target_note_id = p.a_id)))
        order by cardinality(p.shared_tags) desc, similarity desc nulls last, na.title
        limit $3
        """,
        ctx.id,
        note_id,
        limit,
    )

    return [
        {
            "source": {"id": str(r["a_id"]), "title": r["a_title"]},
            "target": {"id": str(r["b_id"]), "title": r["b_title"]},
            "shared_tags": list(r["shared_tags"]),
            # Why this pair is being suggested. The UI shows it, because a
            # suggestion with no stated reason reads as a random guess.
            "reason": (
                f"Both tagged {', '.join(r['shared_tags'])}"
                if r["shared_tags"]
                else "Similar content"
            ),
            "similarity": float(r["similarity"]) if r["similarity"] is not None else None,
        }
        for r in rows
    ]
