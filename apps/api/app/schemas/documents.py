from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProcessingStage = Literal["extracting", "chunking", "embedding", "entities"]

# Notes and documents share a status vocabulary but not a table.
ItemStatus = Literal["pending", "processing", "ready", "failed", "archived"]


class NoteSummary(BaseModel):
    id: str
    kind: Literal["note"] = "note"
    title: str
    status: ItemStatus
    processing_stage: ProcessingStage | None = None
    collection_id: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class NoteDetail(NoteSummary):
    content: str | None = None
    content_format: str = "markdown"
    error_message: str | None = None
    version: int = 1
    embedding_model: str | None = None
    chunk_count: int = 0


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    content_format: Literal["markdown", "plain"] = "markdown"
    collection_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    content: str | None = None
    content_format: Literal["markdown", "plain"] | None = None
    collection_id: str | None = None
    tags: list[str] | None = None


class DocumentSummary(BaseModel):
    id: str
    kind: Literal["document"] = "document"
    title: str
    filename: str
    status: ItemStatus
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
    file_path: str | None = None
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
    file_path: str
    upload_url: str
    expires_at: datetime


class DocumentCreate(BaseModel):
    document_id: str
    title: str = Field(min_length=1, max_length=500)
    # the original name, echoed back from upload-url; file_path is derived
    # server-side from the storage object, never trusted from the client
    filename: str = Field(min_length=1, max_length=255)
    collection_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    collection_id: str | None = None
    tags: list[str] | None = None


class StatusResponse(BaseModel):
    id: str
    kind: Literal["note", "document"]
    status: ItemStatus
    processing_stage: ProcessingStage | None = None
    progress_pct: int
    error_message: str | None = None


class ChunkPreview(BaseModel):
    id: str
    kind: Literal["note", "document"]
    chunk_index: int
    content: str
    heading_path: str | None = None
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    token_count: int | None = None
