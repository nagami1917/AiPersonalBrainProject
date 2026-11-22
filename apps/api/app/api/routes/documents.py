from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core import audit, tenancy
from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser, UserConn, WorkspaceCtx
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.upload_validation import (
    DeclaredFile,
    UploadValidationError,
    validate_declared,
)
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

# Progress is a function of the design's pipeline order (§4.1), so the bar moves
# monotonically and never jumps backwards.
_STAGE_PROGRESS = {
    None: 0,
    "extracting": 20,
    "normalizing": 30,
    "chunking": 45,
    "embedding": 70,
    "entities": 90,
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

    # Rule 9, first half: extension, declared type, size, and that the first two
    # agree. The content half runs in the worker, where the bytes exist.
    try:
        validate_declared(
            DeclaredFile(
                filename=payload.filename,
                mime_type=payload.mime_type,
                file_size=payload.file_size,
            ),
            allowed_mime=settings.allowed_mime_types,
            max_bytes=settings.max_upload_bytes,
        )
    except UploadValidationError as exc:
        raise Unprocessable(exc.message, code=exc.code) from exc

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
    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="document.upload",
        target_type="document",
        target_id=payload.document_id,
        target_label=payload.title,
        detail={"filename": payload.filename, "file_size": size},
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
        # Withheld deliberately (security §7, rule 5): the storage path is not
        # needed by any screen — downloads go through /download-url — and
        # exposing it hands an attacker the exact string to try to manipulate.
        file_path=None,
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
    existing = await repo.get_document(conn, document_id, ctx.id)
    if not await repo.soft_delete_document(conn, document_id, ctx.id):
        raise NotFound("Document not found")
    # Recorded after the delete succeeds but keyed to an id that no longer
    # resolves — which is exactly why audit_events has no FK on target_id.
    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="document.delete",
        target_type="document",
        target_id=document_id,
        target_label=existing["title"] if existing else None,
    )
    await conn.fetchval(
        """select public.enqueue_job(
             p_workspace_id => $1, p_job_type => 'purge', p_document_id => $2)""",
        ctx.id,
        document_id,
    )
    return Response(status_code=204)


@router.get("/{document_id}/download-url", response_model=dict)
async def create_download_url(
    document_id: str,
    ctx: WorkspaceCtx,
    conn: UserConn,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """A short-lived signed URL for the original file.

    Rule 5: users must not be able to download arbitrary storage objects, and
    rule 7: downloads use signed URLs with short expiration. Both are satisfied
    by the same property — **the path never comes from the client**. It comes
    from the document row the caller just proved they can read.

    Why that matters beyond the obvious: a route taking a `path` parameter
    (or echoing one back) would accept `../other-workspace/...`; Supabase's
    policies would still refuse the *bytes* for a different workspace, but the
    API must not be the thing relying on a second system's policy to catch it.
    Membership is checked here, the path is derived here, and the TTL is short
    enough that a leaked URL is a brief problem rather than a permanent one.

    Expiry is returned so the client can refresh instead of showing a broken
    image after a minute.
    """
    record = await repo.get_document(conn, document_id, ctx.id)
    if record is None or not record["file_path"]:
        raise NotFound("Document not found")

    stored_path = str(record["file_path"])
    # Belt and braces: the stored path must still sit inside this workspace's
    # prefix. A row corrupted by any means is refused rather than signed.
    if not stored_path.startswith(f"{ctx.id}/"):
        raise NotFound("Document not found")

    url = storage_service.create_signed_download_url(settings, stored_path)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.signed_download_ttl_seconds)

    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="document.download",
        target_type="document",
        target_id=document_id,
        target_label=record["title"],
    )
    return {
        "url": url,
        "expires_at": expires_at.isoformat(),
        "filename": record["filename"],
        "mime_type": record["mime_type"],
    }


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
    await audit.record(
        conn,
        workspace_id=ctx.id,
        action="document.reprocess",
        target_type="document",
        target_id=document_id,
    )
    return {"job_id": str(job_id), "status": "queued"}
