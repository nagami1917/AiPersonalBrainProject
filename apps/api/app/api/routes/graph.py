from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.deps import UserConn, WorkspaceCtx
from app.core.errors import NotFound

router = APIRouter(prefix="/workspaces/{workspace_id}/graph", tags=["graph"])


@router.get("/entities", response_model=list[dict[str, Any]])
async def list_entities(
    ctx: WorkspaceCtx,
    conn: UserConn,
    q: str | None = None,
    type: str | None = None,  # noqa: A002 - matches the API contract
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select id, name, entity_type, description, mention_count, last_seen_at
           from public.knowledge_entities
           where workspace_id=$1
             and ($2::text is null or name ilike '%' || $2 || '%')
             and ($3::text is null or entity_type = $3)
           order by mention_count desc, lower(name)
           limit $4""",
        ctx.id,
        q,
        type,
        limit,
    )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "entity_type": r["entity_type"],
            "description": r["description"],
            "mention_count": r["mention_count"],
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
        }
        for r in rows
    ]


@router.get("/entities/{entity_id}", response_model=dict)
async def get_entity(entity_id: str, ctx: WorkspaceCtx, conn: UserConn) -> dict[str, Any]:
    entity = await conn.fetchrow(
        """select id, name, entity_type, description, mention_count, last_seen_at
           from public.knowledge_entities where id=$1 and workspace_id=$2""",
        entity_id,
        ctx.id,
    )
    if entity is None:
        raise NotFound("Entity not found")

    links = await conn.fetch(
        """select l.id, l.relation, l.weight,
                  case when l.source_entity_id = $1 then 'out' else 'in' end as direction,
                  e.id as other_id, e.name as other_name, e.entity_type as other_type
           from public.knowledge_links l
           join public.knowledge_entities e
             on e.id = case when l.source_entity_id = $1
                            then l.target_entity_id else l.source_entity_id end
           where l.workspace_id = $2
             and ($1 = l.source_entity_id or $1 = l.target_entity_id)
           order by l.weight desc limit 100""",
        entity_id,
        ctx.id,
    )

    # Which sources mention it — notes and documents are separate tables, so the
    # chunk link tells us which parent to read the title from.
    mentions = await conn.fetch(
        """select ce.confidence, c.id as chunk_id,
                  case when c.note_id is not null then 'note' else 'document' end as kind,
                  coalesce(c.note_id, c.document_id) as source_id,
                  coalesce(n.title, d.title) as source_title
           from public.chunk_entities ce
           join public.document_chunks c on c.id = ce.chunk_id
           left join public.notes n on n.id = c.note_id
           left join public.documents d on d.id = c.document_id
           where ce.entity_id=$1 and ce.workspace_id=$2
           order by ce.confidence desc limit 50""",
        entity_id,
        ctx.id,
    )

    return {
        "id": str(entity["id"]),
        "name": entity["name"],
        "entity_type": entity["entity_type"],
        "description": entity["description"],
        "mention_count": entity["mention_count"],
        "last_seen_at": entity["last_seen_at"].isoformat() if entity["last_seen_at"] else None,
        "links": [
            {
                "id": str(r["id"]),
                "relation": r["relation"],
                "weight": r["weight"],
                "direction": r["direction"],
                "entity": {
                    "id": str(r["other_id"]),
                    "name": r["other_name"],
                    "entity_type": r["other_type"],
                },
            }
            for r in links
        ],
        "mentions": [
            {
                "chunk_id": str(r["chunk_id"]),
                "confidence": r["confidence"],
                "source_kind": r["kind"],
                "source_id": str(r["source_id"]),
                "source_title": r["source_title"],
            }
            for r in mentions
        ],
    }


@router.get("/links", response_model=list[dict[str, Any]])
async def list_links(
    ctx: WorkspaceCtx,
    conn: UserConn,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select l.id, l.relation, l.weight,
                  s.id as source_id, s.name as source_name, s.entity_type as source_type,
                  t.id as target_id, t.name as target_name, t.entity_type as target_type
           from public.knowledge_links l
           join public.knowledge_entities s on s.id = l.source_entity_id
           join public.knowledge_entities t on t.id = l.target_entity_id
           where l.workspace_id=$1
           order by l.weight desc limit $2""",
        ctx.id,
        limit,
    )
    return [
        {
            "id": str(r["id"]),
            "relation": r["relation"],
            "weight": r["weight"],
            "source": {
                "id": str(r["source_id"]),
                "name": r["source_name"],
                "entity_type": r["source_type"],
            },
            "target": {
                "id": str(r["target_id"]),
                "name": r["target_name"],
                "entity_type": r["target_type"],
            },
        }
        for r in rows
    ]
