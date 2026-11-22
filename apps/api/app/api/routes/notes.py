from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.deps import current_user, user_db
from app.core.errors import NotFound
from app.core.security import AuthUser
from app.repositories import documents as repo
from app.schemas.documents import DocumentSummary

router = APIRouter(prefix="/notes", tags=["notes"])


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)
    collection_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class NoteOut(BaseModel):
    id: str
    title: str
    status: str
    job_id: str | None = None


@router.post("", response_model=NoteOut, status_code=202)
async def create_note(
    payload: NoteCreate,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
    settings: Settings = Depends(get_settings),
) -> NoteOut:
    """Notes and uploads share one table (ADR-003): same chunking, same search,
    same citation shape."""
    note_id = str(uuid.uuid4())
    record = await repo.insert_document(
        conn,
        {
            "id": note_id,
            "user_id": user.id,
            "collection_id": payload.collection_id,
            "source_type": "note",
            "title": payload.title,
            "raw_text": payload.body,
            "status": "pending",
            "metadata": {},
        },
    )
    if payload.tags:
        await repo.replace_tags(conn, note_id, user.id, payload.tags)

    job_id = await conn.fetchval(
        "select public.enqueue_job($1,'ingest',$2,'{}'::jsonb,100)", user.id, note_id
    )
    return NoteOut(
        id=str(record["id"]), title=record["title"], status=record["status"], job_id=str(job_id)
    )


@router.get("", response_model=dict)
async def list_notes(
    limit: int = 25,
    collection_id: str | None = None,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    records = await repo.list_documents(
        conn, user.id, source_type="note", collection_id=collection_id, limit=limit
    )
    return {
        "items": [
            DocumentSummary(
                id=str(r["id"]),
                title=r["title"],
                source_type=r["source_type"],
                status=r["status"],
                processing_stage=r["processing_stage"],
                collection_id=str(r["collection_id"]) if r["collection_id"] else None,
                summary=r["summary"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            ).model_dump(mode="json")
            for r in records
        ],
        "next_cursor": None,
    }


@router.get("/{note_id}", response_model=dict)
async def get_note(
    note_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    record = await repo.get(conn, note_id, user.id)
    if record is None or record["source_type"] != "note":
        raise NotFound("Note not found")
    data = dict(record)
    return {
        "id": str(data["id"]),
        "title": data["title"],
        "body": data.get("raw_text"),
        "status": data["status"],
        "version": data.get("version", 1),
        "collection_id": str(data["collection_id"]) if data.get("collection_id") else None,
        "tags": await repo.tag_names(conn, note_id),
        "created_at": data["created_at"].isoformat(),
        "updated_at": data["updated_at"].isoformat(),
    }
