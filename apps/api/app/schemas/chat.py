from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    """Filters applied inside the retrieval SQL, so both arms see the same set."""

    collection_id: str | None = None
    tag: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    # restrict to specific notes/documents (ids of either kind)
    source_ids: list[str] | None = None
    kind: Literal["note", "document"] | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int = Field(default=20, ge=1, le=50)
    include_chunks: bool = True


class SearchResult(BaseModel):
    source_kind: Literal["note", "document"]
    source_id: str
    source_title: str
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
    """The citation contract, one field per requirement in the spec.

    `relevance` + `relevance_kind` replace the old `score` because the old field
    published a raw RRF value (~0.01) that clients rendered as a percentage. The
    kind is part of the payload so the unit travels with the number.
    """

    marker: int
    # --- identity: which chunk, in which document ---------------------------
    chunk_id: str
    source_kind: Literal["note", "document"]
    source_id: str
    # --- human-readable naming, incl. page when the format has one ----------
    source_title: str
    filename: str | None = None
    page: int | None = None
    heading_path: str | None = None
    # --- the retrieved source content the claim rests on --------------------
    quote: str
    # --- relevance ----------------------------------------------------------
    relevance: float
    relevance_kind: Literal["relative"] = "relative"


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


class FeedbackOut(BaseModel):
    helpful: bool
    reason: str | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    grounded: bool | None = None
    created_at: datetime
    citations: list[CitationOut] = Field(default_factory=list)
    # null means unrated; a rated message is always an object
    feedback: FeedbackOut | None = None


class FeedbackRequest(BaseModel):
    helpful: bool
    reason: str | None = Field(default=None, max_length=500)
