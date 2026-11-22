from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core import tenancy
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx
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
                  case when l.source_id = $1 then 'out' else 'in' end as direction,
                  e.id as other_id, e.name as other_name, e.entity_type as other_type
           from public.knowledge_links l
           join public.knowledge_entities e
             on e.id = case when l.source_id = $1
                            then l.target_id else l.source_id end
           where l.workspace_id = $2
             and ($1 = l.source_id or $1 = l.target_id)
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
           join public.knowledge_entities s on s.id = l.source_id
           join public.knowledge_entities t on t.id = l.target_id
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


# ---------------------------------------------------------------------------
# Flat shape for clients that want "the graph" in one call.
#
# The scoped routes above answer narrower questions (search entities, page through
# links). A graph view needs both at once, and making the client fan out to two
# endpoints means two round trips that can disagree — a node rendered with no edges
# because the second response had not arrived. This returns one consistent snapshot.
# ---------------------------------------------------------------------------


async def _graph_payload(conn: Any, workspace_id: str, limit: int) -> dict[str, Any]:
    entities = await conn.fetch(
        """select id, name, entity_type, description, mention_count
           from public.knowledge_entities
           where workspace_id=$1
           order by mention_count desc, lower(name)
           limit $2""",
        workspace_id,
        limit,
    )
    links = await conn.fetch(
        """select l.id, l.relation, l.weight, l.source_id, l.target_id
           from public.knowledge_links l
           where l.workspace_id=$1
           order by l.weight desc
           limit $2""",
        workspace_id,
        limit * 4,
    )
    return {
        "nodes": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "entity_type": r["entity_type"],
                "description": r["description"],
                "mention_count": r["mention_count"],
            }
            for r in entities
        ],
        "edges": [
            {
                "id": str(r["id"]),
                "source_id": str(r["source_id"]),
                "target_id": str(r["target_id"]),
                "relation": r["relation"],
                "weight": r["weight"],
            }
            for r in links
        ],
    }


flat_router = APIRouter(prefix="/graph", tags=["graph"])


@flat_router.get("", response_model=dict)
async def flat_graph(
    conn: UserConn,
    user: CurrentUser,
    workspace_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Graph data in one snapshot.

    `workspace_id` defaults to the caller's own default workspace, so a single-user
    client never has to know the id. When supplied it is authorised the same way as
    the path form — membership, or 404.
    """
    ctx = (
        await tenancy.resolve_workspace(conn, user_id=user.id, workspace_id=workspace_id)
        if workspace_id
        else await tenancy.default_workspace(conn, user_id=user.id)
    )
    payload = await _graph_payload(conn, ctx.id, limit)
    payload["workspace_id"] = ctx.id
    return payload
