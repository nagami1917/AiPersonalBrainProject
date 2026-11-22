from __future__ import annotations

import re
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.config import Settings, get_settings
from app.core.deps import current_user, user_db
from app.core.errors import Conflict, NotFound, PayloadTooLarge, Unprocessable
from app.core.security import AuthUser
from app.repositories import documents as repo
from app.schemas.documents import (
    ChunkPreview,
    DocumentCreate,
    DocumentDetail,
    DocumentStatusResponse,
    DocumentSummary,
    DocumentUpdate,
    UploadUrlRequest,
    UploadUrlResponse,
)
from app.services import storage_service

router = APIRouter(prefix="/documents", tags=["documents"])

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
        source_type=data["source_type"],
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


@router.post("/upload-url", response_model=UploadUrlResponse)
async def create_upload_url(
    payload: UploadUrlRequest,
    user: AuthUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> UploadUrlResponse:
    if payload.file_size > settings.max_upload_bytes:
        raise PayloadTooLarge(
            f"File is larger than the {settings.max_upload_bytes // (1024*1024)} MB limit"
        )
    if payload.mime_type not in settings.allowed_mime_types:
        raise Unprocessable(
            f"Unsupported file type: {payload.mime_type}", code="unsupported_file_type"
        )

    document_id = str(uuid.uuid4())
    storage_path = f"{user.id}/{document_id}/{_safe_filename(payload.filename)}"
    upload_url, expires_at = storage_service.create_signed_upload_url(settings, storage_path)
    return UploadUrlResponse(
        document_id=document_id,
        storage_path=storage_path,
        upload_url=upload_url,
        expires_at=expires_at,
    )


@router.post("", response_model=dict, status_code=201)
async def confirm_upload(
    payload: DocumentCreate,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    storage_path = f"{user.id}/{payload.document_id}/"
    exists = await conn.fetchval(
        """select 1 from storage.objects
           where bucket_id=$1 and name like $2 || '%' limit 1""",
        settings.storage_bucket,
        storage_path,
    )
    if not exists:
        raise NotFound("No uploaded file found for this document id")

    object_name = await conn.fetchval(
        """select name from storage.objects
           where bucket_id=$1 and name like $2 || '%' limit 1""",
        settings.storage_bucket,
        storage_path,
    )
    size = await conn.fetchval(
        "select (metadata->>'size')::bigint from storage.objects where name=$1", object_name
    )

    duplicate = await conn.fetchrow(
        """select id, title from public.documents
           where user_id=$1 and deleted_at is null and storage_path=$2""",
        user.id,
        object_name,
    )
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
            "user_id": user.id,
            "collection_id": payload.collection_id,
            "source_type": "upload",
            "title": payload.title,
            "mime_type": _mime_from_name(object_name),
            "storage_path": object_name,
            "file_size": size,
            "status": "pending",
            "metadata": {},
        },
    )
    if payload.tags:
        await repo.replace_tags(conn, payload.document_id, user.id, payload.tags)

    job_id = await conn.fetchval(
        "select public.enqueue_job($1,'ingest',$2,'{}'::jsonb,100)",
        user.id,
        payload.document_id,
    )
    return {
        "document": _summary_record(record, payload.tags).model_dump(mode="json"),
        "job_id": str(job_id),
    }


def _mime_from_name(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".md"):
        return "text/markdown"
    return "text/plain"


@router.get("", response_model=dict)
async def list_documents(
    status: str | None = None,
    collection_id: str | None = None,
    source_type: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    records = await repo.list_documents(
        conn,
        user.id,
        status=status,
        collection_id=collection_id,
        source_type=source_type,
        limit=limit,
        cursor=cursor,
    )
    items = [_summary_record(r).model_dump(mode="json") for r in records]
    next_cursor = (
        records[-1]["updated_at"].isoformat() if len(records) == limit and records else None
    )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> DocumentDetail:
    record = await repo.get(conn, document_id, user.id)
    if record is None:
        raise NotFound("Document not found")
    tags = await repo.tag_names(conn, document_id)
    data = dict(record)
    base = _summary_record(record, tags)
    return DocumentDetail(
        **base.model_dump(),
        raw_text=data.get("raw_text"),
        storage_path=data.get("storage_path"),
        error_message=data.get("error_message"),
        version=data.get("version", 1),
        embedding_model=data.get("embedding_model"),
        chunk_count=await repo.chunk_count(conn, document_id),
    )


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
async def get_status(
    document_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> DocumentStatusResponse:
    record = await repo.get(conn, document_id, user.id)
    if record is None:
        raise NotFound("Document not found")
    data = dict(record)
    status = data["status"]
    stage = data.get("processing_stage")
    progress = (
        100 if status == "ready" else _STAGE_PROGRESS.get(stage, 5 if status == "pending" else 10)
    )
    return DocumentStatusResponse(
        id=str(data["id"]),
        status=status,
        processing_stage=stage,
        progress_pct=progress,
        error_message=data.get("error_message"),
    )


@router.patch("/{document_id}", response_model=DocumentSummary)
async def update_document(
    document_id: str,
    payload: DocumentUpdate,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> DocumentSummary:
    existing = await repo.get(conn, document_id, user.id)
    if existing is None:
        raise NotFound("Document not found")

    patch: dict[str, Any] = {}
    if payload.title is not None:
        patch["title"] = payload.title
    if payload.collection_id is not None or "collection_id" in payload.model_fields_set:
        patch["collection_id"] = payload.collection_id

    if payload.raw_text is not None:
        if existing["source_type"] != "note":
            raise Unprocessable(
                "Only notes can have their body edited directly", code="invalid_request"
            )
        patch["raw_text"] = payload.raw_text
        await repo.bump_version(conn, document_id, user.id)

    record = await repo.update_document(conn, document_id, user.id, patch) or existing

    if payload.tags is not None:
        await repo.replace_tags(conn, document_id, user.id, payload.tags)

    if payload.raw_text is not None:
        await conn.fetchval(
            "select public.enqueue_job($1,'reindex',$2,'{}'::jsonb,100)", user.id, document_id
        )

    tags = await repo.tag_names(conn, document_id)
    return _summary_record(record, tags)


@router.delete("/{document_id}", status_code=204, response_class=Response, response_model=None)
async def delete_document(
    document_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> Response:
    deleted = await repo.soft_delete(conn, document_id, user.id)
    if not deleted:
        raise NotFound("Document not found")
    await conn.fetchval(
        "select public.enqueue_job($1,'purge',$2,'{}'::jsonb,200)", user.id, document_id
    )
    return Response(status_code=204)


@router.post("/{document_id}/reprocess", status_code=202)
async def reprocess(
    document_id: str,
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> dict[str, Any]:
    record = await repo.get(conn, document_id, user.id)
    if record is None:
        raise NotFound("Document not found")
    job_id = await conn.fetchval(
        "select public.enqueue_job($1,'reindex',$2,$3::jsonb,50)",
        user.id,
        document_id,
        {"force": True},
    )
    return {"job_id": str(job_id), "status": "queued"}


@router.get("/{document_id}/chunks", response_model=list[ChunkPreview])
async def list_chunks(
    document_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: AuthUser = Depends(current_user),
    conn: asyncpg.Connection = Depends(user_db),
) -> list[ChunkPreview]:
    record = await repo.get(conn, document_id, user.id)
    if record is None:
        raise NotFound("Document not found")
    rows = await conn.fetch(
        """select id, ordinal, content, heading_path, page_start, char_start, char_end, token_count
           from public.chunks where document_id=$1 order by ordinal limit $2 offset $3""",
        document_id,
        limit,
        offset,
    )
    return [
        ChunkPreview(
            id=str(r["id"]),
            ordinal=r["ordinal"],
            content=r["content"],
            heading_path=r["heading_path"],
            page_start=r["page_start"],
            char_start=r["char_start"],
            char_end=r["char_end"],
            token_count=r["token_count"],
        )
        for r in rows
    ]
