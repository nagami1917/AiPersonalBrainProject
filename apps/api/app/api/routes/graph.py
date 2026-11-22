from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.core.deps import current_user, user_db
from app.core.errors import NotFound
from app.core.security import AuthUser

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/entities", response_model=list[dict[str, Any]])
async def list_entities(
    q: str | None = None,
    type: str | None = None,  # noqa: A002 - matches the API contract
    limit: int = Query(default=50, ge=1, le=200),
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select id, name, entity_type, description, mention_count, last_seen_at
           from public.entities
           where user_id=$1
             and ($2::text is null or name ilike '%' || $2 || '%')
             and ($3::text is null or entity_type = $3)
           order by mention_count desc, lower(name)
           limit $4""",
        user.id,
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
async def get_entity(
    entity_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    entity = await conn.fetchrow(
        "select * from public.entities where id=$1 and user_id=$2", entity_id, user.id
    )
    if entity is None:
        raise NotFound("Entity not found")

    sources = await conn.fetch(
        """select d.id as document_id, d.title, c.id as chunk_id, c.page_start, ce.confidence
           from public.chunk_entities ce
           join public.chunks c on c.id = ce.chunk_id
           join public.documents d on d.id = c.document_id
           where ce.entity_id=$1 and d.deleted_at is null
           order by ce.confidence desc limit 20""",
        entity_id,
    )
    return {
        "id": str(entity["id"]),
        "name": entity["name"],
        "entity_type": entity["entity_type"],
        "description": entity["description"],
        "mention_count": entity["mention_count"],
        "sources": [
            {
                "document_id": str(s["document_id"]),
                "document_title": s["title"],
                "chunk_id": str(s["chunk_id"]),
                "page": s["page_start"],
                "confidence": float(s["confidence"]),
            }
            for s in sources
        ],
    }


@router.get("/entities/{entity_id}/neighbors", response_model=dict)
async def neighbors(
    entity_id: str,
    depth: int = Query(default=1, ge=1, le=2),
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    """1–2 hop slice. Depth is capped in the API, never unbounded in SQL."""
    root = await conn.fetchval(
        "select id from public.entities where id=$1 and user_id=$2", entity_id, user.id
    )
    if root is None:
        raise NotFound("Entity not found")

    edges = await conn.fetch(
        """with recursive walk(source, target, relation, weight, depth) as (
             select source_entity_id, target_entity_id, relation, weight, 1
             from public.entity_edges
             where user_id=$1 and source_entity_id=$2
             union all
             select e.source_entity_id, e.target_entity_id, e.relation, e.weight, w.depth+1
             from public.entity_edges e
             join walk w on e.source_entity_id = w.target
             where e.user_id=$1 and w.depth < $3
           )
           select distinct on (source, target) source, target, relation, weight
           from walk limit 200""",
        user.id,
        entity_id,
        depth,
    )

    node_ids = {entity_id}
    for edge in edges:
        node_ids.add(str(edge["source"]))
        node_ids.add(str(edge["target"]))

    nodes = await conn.fetch(
        """select id, name, entity_type, mention_count from public.entities
           where id = any($1::uuid[])""",
        list(node_ids),
    )
    return {
        "nodes": [
            {
                "id": str(n["id"]),
                "name": n["name"],
                "entity_type": n["entity_type"],
                "mention_count": n["mention_count"],
            }
            for n in nodes
        ],
        "edges": [
            {
                "source": str(e["source"]),
                "target": str(e["target"]),
                "relation": e["relation"],
                "weight": e["weight"],
            }
            for e in edges
        ],
    }
