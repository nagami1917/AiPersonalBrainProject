from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core import tenancy
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx
from app.core.errors import Forbidden, NotFound
from app.repositories import knowledge as repo
from app.schemas.documents import NoteCreate, NoteDetail, NoteSummary, NoteUpdate, StatusResponse

router = APIRouter(prefix="/workspaces/{workspace_id}/notes", tags=["notes"])

_STAGE_PROGRESS = {
    None: 0,
    "extracting": 20,
    "normalizing": 30,
    "chunking": 45,
    "embedding": 70,
    "entities": 90,
}


def _summary(record: asyncpg.Record, tags: list[str] | None = None) -> NoteSummary:
    data = dict(record)
    return NoteSummary(
        id=str(data["id"]),
        title=data["title"],
        status=data["status"],
        processing_stage=data.get("processing_stage"),
        collection_id=str(data["collection_id"]) if data.get("collection_id") else None,
        summary=data.get("summary"),
        tags=tags or [],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


@router.get("", response_model=dict)
async def list_notes(
    ctx: WorkspaceCtx,
    conn: UserConn,
    status: str | None = None,
    collection_id: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    records = await repo.list_notes(
        conn, ctx.id, status=status, collection_id=collection_id, limit=limit, cursor=cursor
    )
    items = [_summary(r).model_dump(mode="json") for r in records]
    next_cursor = records[-1]["updated_at"].isoformat() if len(records) == limit else None
    return {"items": items, "next_cursor": next_cursor}


@router.post("", response_model=dict, status_code=202)
async def create_note(
    payload: NoteCreate,
    ctx: WorkspaceCtx,
    conn: UserConn,
    user: CurrentUser,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    tenancy.require_write(ctx)

    note_id = str(uuid.uuid4())
    record = await repo.insert_note(
        conn,
        {
            "id": note_id,
            "workspace_id": ctx.id,
            "collection_id": payload.collection_id,
            "created_by": user.id,
            "title": payload.title,
            "content": payload.content,
            "content_format": payload.content_format,
            "status": "pending",
        },
    )
    if payload.tags:
        await repo.replace_tags(conn, ctx.id, payload.tags, note_id=note_id)

    job_id = await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'ingest_note', p_note_id => $2)""",
        ctx.id,
        note_id,
    )
    return {
        "note": _summary(record, payload.tags).model_dump(mode="json"),
        "job_id": str(job_id),
    }


@router.get("/{note_id}", response_model=NoteDetail)
async def get_note(note_id: str, ctx: WorkspaceCtx, conn: UserConn) -> NoteDetail:
    record = await repo.get_note(conn, note_id, ctx.id)
    if record is None:
        raise NotFound("Note not found")

    data = dict(record)
    tags = await repo.tag_names(conn, workspace_id=ctx.id, note_id=note_id)
    return NoteDetail(
        **_summary(record, tags).model_dump(),
        content=data.get("content"),
        content_format=data.get("content_format", "markdown"),
        error_message=data.get("error_message"),
        version=data.get("version", 1),
        embedding_model=data.get("embedding_model"),
        chunk_count=await repo.chunk_count(conn, ctx.id, note_id=note_id),
    )


@router.patch("/{note_id}", response_model=NoteSummary)
async def update_note(
    note_id: str, payload: NoteUpdate, ctx: WorkspaceCtx, conn: UserConn
) -> NoteSummary:
    tenancy.require_write(ctx)
    existing = await repo.get_note(conn, note_id, ctx.id)
    if existing is None:
        raise NotFound("Note not found")

    patch: dict[str, Any] = {}
    if payload.title is not None:
        patch["title"] = payload.title
    if "collection_id" in payload.model_fields_set:
        patch["collection_id"] = payload.collection_id
    if payload.content is not None:
        patch["content"] = payload.content
        await repo.bump_note_version(conn, note_id, ctx.id)
    if payload.content_format is not None:
        patch["content_format"] = payload.content_format

    record = await repo.update_note(conn, note_id, ctx.id, patch) or existing

    if payload.tags is not None:
        await repo.replace_tags(conn, ctx.id, payload.tags, note_id=note_id)

    # A content edit changes the searchable representation, so re-index.
    if payload.content is not None:
        await conn.fetchval(
            """select public.enqueue_job(
                 p_workspace_id => $1, p_job_type => 'ingest_note', p_note_id => $2)""",
            ctx.id,
            note_id,
        )

    return _summary(record, await repo.tag_names(conn, workspace_id=ctx.id, note_id=note_id))


@router.delete("/{note_id}", status_code=204, response_class=Response, response_model=None)
async def delete_note(note_id: str, ctx: WorkspaceCtx, conn: UserConn) -> Response:
    tenancy.require_write(ctx)
    if not await repo.soft_delete_note(conn, note_id, ctx.id):
        raise NotFound("Note not found")
    await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'purge', p_note_id => $2)""",
        ctx.id,
        note_id,
    )
    return Response(status_code=204)


@router.get("/{note_id}/status", response_model=StatusResponse)
async def note_status(note_id: str, ctx: WorkspaceCtx, conn: UserConn) -> StatusResponse:
    record = await repo.get_note(conn, note_id, ctx.id)
    if record is None:
        raise NotFound("Note not found")
    data = dict(record)
    status = data["status"]
    stage = data.get("processing_stage")
    return StatusResponse(
        id=str(data["id"]),
        kind="note",
        status=status,
        processing_stage=stage,
        progress_pct=100
        if status == "ready"
        else _STAGE_PROGRESS.get(stage, 5 if status == "pending" else 10),
        error_message=data.get("error_message"),
    )


@router.post("/{note_id}/reprocess", status_code=202)
async def reprocess_note(note_id: str, ctx: WorkspaceCtx, conn: UserConn) -> dict[str, Any]:
    tenancy.require_write(ctx)
    if await repo.get_note(conn, note_id, ctx.id) is None:
        raise NotFound("Note not found")
    job_id = await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'reindex', p_note_id => $2, p_priority => 50)""",
        ctx.id,
        note_id,
    )
    return {"job_id": str(job_id), "status": "queued"}


def _forbidden() -> None:
    raise Forbidden("read-only", code="insufficient_role")
