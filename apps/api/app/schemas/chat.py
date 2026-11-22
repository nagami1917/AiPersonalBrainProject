from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    collection_id: str | None = None
    tag: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    document_ids: list[str] | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int = Field(default=20, ge=1, le=50)
    include_chunks: bool = True


class SearchResult(BaseModel):
    document_id: str
    document_title: str
    chunk_id: str
    snippet: str
    heading_path: str | None = None
    page: int | None = None
    score: float
    matched_arm: str


class SearchResponse(BaseModel):
    results: list[SearchResult]
    total_candidates: dict[str, int]


class ChatOptions(BaseModel):
    max_sources: int = Field(default=8, ge=1, le=20)
    answer_style: Literal["concise", "detailed"] = "concise"


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    options: ChatOptions = Field(default_factory=ChatOptions)


class CitationOut(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    quote: str
    page: int | None = None
    heading_path: str | None = None
    score: float


class ChatResponse(BaseModel):
    message_id: str
    conversation_id: str
    content: str
    grounded: bool
    citations: list[CitationOut] = Field(default_factory=list)
    latency_ms: int | None = None


class ConversationOut(BaseModel):
    id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    grounded: bool | None = None
    created_at: datetime
    citations: list[CitationOut] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    helpful: bool
    reason: str | None = Field(default=None, max_length=500)
