from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.deps import current_user, user_db
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.security import AuthUser

router = APIRouter(prefix="/collections", tags=["collections"])
tags_router = APIRouter(prefix="/tags", tags=["tags"])

MAX_DEPTH = 3


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None
    description: str | None = None
    color: str | None = None


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: str | None = None
    description: str | None = None
    color: str | None = None


def _row(record: asyncpg.Record) -> dict[str, Any]:
    data = dict(record)
    return {
        "id": str(data["id"]),
        "name": data["name"],
        "parent_id": str(data["parent_id"]) if data.get("parent_id") else None,
        "description": data.get("description"),
        "color": data.get("color"),
        "position": data.get("position", 0),
        "created_at": data["created_at"].isoformat(),
        "updated_at": data["updated_at"].isoformat(),
    }


async def _depth(conn: asyncpg.Connection, parent_id: str | None, user_id: str) -> int:
    """Walk to the root. Depth is capped here rather than in SQL so the error
    can explain itself."""
    depth = 1
    current = parent_id
    while current:
        depth += 1
        if depth > MAX_DEPTH + 1:
            raise Unprocessable(f"Collections cannot nest deeper than {MAX_DEPTH} levels")
        current = await conn.fetchval(
            "select parent_id from public.collections where id=$1 and user_id=$2",
            current,
            user_id,
        )
    return depth


@router.get("", response_model=list[dict[str, Any]])
async def list_collections(
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select c.*, (select count(*) from public.documents d
                         where d.collection_id = c.id and d.deleted_at is null) as document_count
           from public.collections c where c.user_id=$1
           order by c.position, lower(c.name)""",
        user.id,
    )
    out = []
    for record in rows:
        item = _row(record)
        item["document_count"] = record["document_count"]
        out.append(item)
    return out


@router.post("", response_model=dict, status_code=201)
async def create_collection(
    payload: CollectionCreate,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    if payload.parent_id:
        parent = await conn.fetchval(
            "select id from public.collections where id=$1 and user_id=$2",
            payload.parent_id,
            user.id,
        )
        if not parent:
            raise NotFound("Parent collection not found")
    await _depth(conn, payload.parent_id, user.id)

    try:
        record = await conn.fetchrow(
            """insert into public.collections (user_id, parent_id, name, description, color)
               values ($1,$2,$3,$4,$5) returning *""",
            user.id,
            payload.parent_id,
            payload.name.strip(),
            payload.description,
            payload.color,
        )
    except asyncpg.UniqueViolationError as exc:
        raise Conflict(
            "A collection with that name already exists at this level",
            code="duplicate_collection",
        ) from exc
    return _row(record)


@router.patch("/{collection_id}", response_model=dict)
async def update_collection(
    collection_id: str,
    payload: CollectionUpdate,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    exists = await conn.fetchval(
        "select id from public.collections where id=$1 and user_id=$2", collection_id, user.id
    )
    if not exists:
        raise NotFound("Collection not found")

    if "parent_id" in payload.model_fields_set and payload.parent_id == collection_id:
        raise Unprocessable("A collection cannot be its own parent")

    record = await conn.fetchrow(
        """update public.collections
           set name = coalesce($3, name),
               description = coalesce($4, description),
               color = coalesce($5, color),
               parent_id = case when $6 then $7::uuid else parent_id end
           where id=$1 and user_id=$2 returning *""",
        collection_id,
        user.id,
        payload.name.strip() if payload.name else None,
        payload.description,
        payload.color,
        "parent_id" in payload.model_fields_set,
        payload.parent_id,
    )
    return _row(record)


@router.delete("/{collection_id}", status_code=204, response_class=Response, response_model=None)
async def delete_collection(
    collection_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> None:
    # Documents are NOT deleted with the folder — they fall back to uncategorized.
    result = await conn.execute(
        "delete from public.collections where id=$1 and user_id=$2", collection_id, user.id
    )
    if not result.endswith("1"):
        raise NotFound("Collection not found")


@tags_router.get("", response_model=list[dict[str, Any]])
async def list_tags(
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """select t.id, t.name, count(dt.document_id) as document_count
           from public.tags t
           left join public.document_tags dt on dt.tag_id = t.id
           where t.user_id = $1
           group by t.id, t.name
           order by document_count desc, lower(t.name)""",
        user.id,
    )
    return [
        {"id": str(r["id"]), "name": r["name"], "document_count": r["document_count"]} for r in rows
    ]
