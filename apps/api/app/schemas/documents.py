from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DocumentStatus = Literal["pending", "processing", "ready", "failed", "archived"]
SourceType = Literal["upload", "note", "conversation"]
ProcessingStage = Literal["extracting", "chunking", "embedding", "entities"]


class DocumentSummary(BaseModel):
    id: str
    title: str
    source_type: SourceType
    status: DocumentStatus
    processing_stage: ProcessingStage | None = None
    collection_id: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    page_count: int | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentSummary):
    raw_text: str | None = None
    storage_path: str | None = None
    error_message: str | None = None
    version: int = 1
    embedding_model: str | None = None
    chunk_count: int = 0


class UploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    file_size: int = Field(gt=0)


class UploadUrlResponse(BaseModel):
    document_id: str
    storage_path: str
    upload_url: str
    expires_at: datetime


class DocumentCreate(BaseModel):
    document_id: str
    title: str = Field(min_length=1, max_length=500)
    collection_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    collection_id: str | None = None
    tags: list[str] | None = None
    raw_text: str | None = None


class DocumentStatusResponse(BaseModel):
    id: str
    status: DocumentStatus
    processing_stage: ProcessingStage | None = None
    progress_pct: int
    error_message: str | None = None


class ChunkPreview(BaseModel):
    id: str
    ordinal: int
    content: str
    heading_path: str | None = None
    page_start: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    token_count: int | None = None


class Page(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None
