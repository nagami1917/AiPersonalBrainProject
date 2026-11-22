from __future__ import annotations

import re
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core import tenancy
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx
from app.core.errors import Conflict, NotFound, PayloadTooLarge, Unprocessable
from app.repositories import knowledge as repo
from app.schemas.documents import (
    ChunkPreview,
    DocumentCreate,
    DocumentDetail,
    DocumentSummary,
    DocumentUpdate,
    StatusResponse,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.services import storage_service

router = APIRouter(prefix="/workspaces/{workspace_id}/documents", tags=["documents"])

_STAGE_PROGRESS = {
    None: 0,
    "extracting": 25,
    "chunking": 45,
    "embedding": 75,
    "entities": 95,
}


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (cleaned or "file")[:200]


def _summary_record(record: asyncpg.Record, tags: list[str] | None = None) -> DocumentSummary:
    data = dict(record)
    return DocumentSummary(
        id=str(data["id"]),
        title=data["title"],
        filename=data["filename"],
        status=data["status"],
        processing_stage=data.get("processing_stage"),
        collection_id=str(data["collection_id"]) if data.get("collection_id") else None,
        mime_type=data.get("mime_type"),
        file_size=data.get("file_size"),
        page_count=data.get("page_count"),
        summary=data.get("summary"),
        tags=tags or [],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _mime_from_name(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".md"):
        return "text/markdown"
    return "text/plain"


# ---------------------------------------------------------------------------
# Upload handshake
#
# The browser uploads straight to Supabase Storage with a short-lived signed
# URL. The API never proxies bytes and never sees the file. The storage path is
# {workspace_id}/{document_id}/{filename}, and the bucket's RLS policies parse
# the first segment as a workspace id — so the tenant boundary is enforced on
# the storage side too, not just in the database.
# ---------------------------------------------------------------------------


@router.post("/upload-url", response_model=UploadUrlResponse)
async def create_upload_url(
    payload: UploadUrlRequest,
    ctx: WorkspaceCtx,
    settings: Settings = Depends(get_settings),
) -> UploadUrlResponse:
    tenancy.require_write(ctx)

    if payload.file_size > settings.max_upload_bytes:
        raise PayloadTooLarge(
            f"File is larger than the {settings.max_upload_bytes // (1024 * 1024)} MB limit"
        )
    if payload.mime_type not in settings.allowed_mime_types:
        raise Unprocessable(
            f"Unsupported file type: {payload.mime_type}", code="unsupported_file_type"
        )

    document_id = str(uuid.uuid4())
    file_path = f"{ctx.id}/{document_id}/{_safe_filename(payload.filename)}"
    upload_url, expires_at = storage_service.create_signed_upload_url(settings, file_path)
    return UploadUrlResponse(
        document_id=document_id,
        file_path=file_path,
        upload_url=upload_url,
        expires_at=expires_at,
    )


@router.post("", response_model=dict, status_code=201)
async def confirm_upload(
    payload: DocumentCreate,
    ctx: WorkspaceCtx,
    conn: UserConn,
    user: CurrentUser,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    tenancy.require_write(ctx)

    prefix = f"{ctx.id}/{payload.document_id}/"
    object_name = await conn.fetchval(
        """select name from storage.objects
           where bucket_id=$1 and name like $2 || '%' limit 1""",
        settings.storage_bucket,
        prefix,
    )
    if not object_name:
        raise NotFound("No uploaded file found for this document id")

    size = await conn.fetchval(
        "select (metadata->>'size')::bigint from storage.objects where name=$1", object_name
    )
    checksum = await conn.fetchval(
        "select metadata->>'sha256' from storage.objects where name=$1", object_name
    )

    if checksum:
        duplicate = await repo.find_document_by_checksum(conn, ctx.id, checksum)
        if duplicate:
            raise Conflict(
                "This document has already been imported",
                code="duplicate_document",
                details={"document_id": str(duplicate["id"])},
            )

    record = await repo.insert_document(
        conn,
        {
            "id": payload.document_id,
            "workspace_id": ctx.id,
            "collection_id": payload.collection_id,
            "uploaded_by": user.id,
            "title": payload.title,
            "filename": payload.filename,
            "mime_type": _mime_from_name(object_name),
            "file_path": object_name,
            "file_size": size,
            "checksum_sha256": checksum,
            "status": "pending",
        },
    )
    if payload.tags:
        await repo.replace_tags(conn, ctx.id, payload.tags, document_id=payload.document_id)

    job_id = await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'ingest_document',
             p_document_id => $2)""",
        ctx.id,
        payload.document_id,
    )
    return {
        "document": _summary_record(record, payload.tags).model_dump(mode="json"),
        "job_id": str(job_id),
    }


@router.get("", response_model=dict)
async def list_documents(
    ctx: WorkspaceCtx,
    conn: UserConn,
    status: str | None = None,
    collection_id: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    records = await repo.list_documents(
        conn, ctx.id, status=status, collection_id=collection_id, limit=limit, cursor=cursor
    )
    items = [_summary_record(r).model_dump(mode="json") for r in records]
    next_cursor = (
        records[-1]["updated_at"].isoformat() if len(records) == limit and records else None
    )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: str, ctx: WorkspaceCtx, conn: UserConn) -> DocumentDetail:
    record = await repo.get_document(conn, document_id, ctx.id)
    if record is None:
        raise NotFound("Document not found")
    tags = await repo.tag_names(conn, workspace_id=ctx.id, document_id=document_id)
    data = dict(record)
    return DocumentDetail(
        **_summary_record(record, tags).model_dump(),
        raw_text=data.get("raw_text"),
        file_path=data.get("file_path"),
        error_message=data.get("error_message"),
        version=data.get("version", 1),
        embedding_model=data.get("embedding_model"),
        chunk_count=await repo.chunk_count(conn, ctx.id, document_id=document_id),
    )


@router.patch("/{document_id}", response_model=DocumentSummary)
async def update_document(
    document_id: str, payload: DocumentUpdate, ctx: WorkspaceCtx, conn: UserConn
) -> DocumentSummary:
    tenancy.require_write(ctx)
    if await repo.get_document(conn, document_id, ctx.id) is None:
        raise NotFound("Document not found")

    patch: dict[str, Any] = {}
    if payload.title is not None:
        patch["title"] = payload.title
    if "collection_id" in payload.model_fields_set:
        patch["collection_id"] = payload.collection_id

    record = await repo.update_document(conn, document_id, ctx.id, patch)
    if record is None:
        raise NotFound("Document not found")

    if payload.tags is not None:
        await repo.replace_tags(conn, ctx.id, payload.tags, document_id=document_id)

    return _summary_record(
        record, await repo.tag_names(conn, workspace_id=ctx.id, document_id=document_id)
    )


@router.delete("/{document_id}", status_code=204, response_class=Response, response_model=None)
async def delete_document(document_id: str, ctx: WorkspaceCtx, conn: UserConn) -> Response:
    tenancy.require_write(ctx)
    if not await repo.soft_delete_document(conn, document_id, ctx.id):
        raise NotFound("Document not found")
    await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'purge', p_document_id => $2)""",
        ctx.id,
        document_id,
    )
    return Response(status_code=204)


@router.get("/{document_id}/status", response_model=StatusResponse)
async def get_status(document_id: str, ctx: WorkspaceCtx, conn: UserConn) -> StatusResponse:
    record = await repo.get_document(conn, document_id, ctx.id)
    if record is None:
        raise NotFound("Document not found")
    data = dict(record)
    status = data["status"]
    stage = data.get("processing_stage")
    progress = (
        100 if status == "ready" else _STAGE_PROGRESS.get(stage, 5 if status == "pending" else 10)
    )
    return StatusResponse(
        id=str(data["id"]),
        kind="document",
        status=status,
        processing_stage=stage,
        progress_pct=progress,
        error_message=data.get("error_message"),
    )


@router.get("/{document_id}/chunks", response_model=list[ChunkPreview])
async def list_chunks(
    document_id: str,
    ctx: WorkspaceCtx,
    conn: UserConn,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ChunkPreview]:
    if await repo.get_document(conn, document_id, ctx.id) is None:
        raise NotFound("Document not found")
    rows = await repo.list_chunks(conn, ctx.id, document_id=document_id, limit=limit, offset=offset)
    return [
        ChunkPreview(
            id=str(r["id"]),
            kind="document",
            chunk_index=r["chunk_index"],
            content=r["content"],
            heading_path=r["heading_path"],
            page_number=r["page_number"],
            char_start=r["char_start"],
            char_end=r["char_end"],
            token_count=r["token_count"],
        )
        for r in rows
    ]


@router.post("/{document_id}/reprocess", status_code=202)
async def reprocess(document_id: str, ctx: WorkspaceCtx, conn: UserConn) -> dict[str, Any]:
    tenancy.require_write(ctx)
    if await repo.get_document(conn, document_id, ctx.id) is None:
        raise NotFound("Document not found")
    job_id = await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'reindex',
             p_document_id => $2, p_priority => 50)""",
        ctx.id,
        document_id,
    )
    return {"job_id": str(job_id), "status": "queued"}
