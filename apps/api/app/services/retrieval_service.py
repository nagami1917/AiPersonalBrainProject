"""Retrieval orchestration: embed → SQL hybrid search → diversify → sources."""

from __future__ import annotations

import logging

import asyncpg

from app.ai.grounding import Source
from app.ai.providers import Embedder
from app.ai.retrieval import Hit, dedupe, limit_per_document, row_to_hit, to_sources
from app.core.config import Settings
from app.core.errors import ProviderUnavailable
from app.repositories import search as search_repo
from app.schemas.chat import SearchFilters

log = logging.getLogger(__name__)


class RetrievalResult:
    def __init__(
        self,
        hits: list[Hit],
        sources: list[Source],
        vector_candidates: int,
        keyword_candidates: int,
    ) -> None:
        self.hits = hits
        self.sources = sources
        self.vector_candidates = vector_candidates
        self.keyword_candidates = keyword_candidates


async def retrieve(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    query: str,
    embedder: Embedder,
    settings: Settings,
    filters: SearchFilters | None = None,
    match_count: int | None = None,
    mode: str = "hybrid",
) -> RetrievalResult:
    filters = filters or SearchFilters()
    embedding: list[float] | None = None

    if mode != "keyword":
        try:
            vectors = await embedder.embed([query])
            embedding = vectors[0] if vectors else None
        except ProviderUnavailable:
            # Deliberately NOT a silent keyword-only fallback for chat: the
            # caller decides, because presenting degraded retrieval as full
            # quality would misrepresent the answer's grounding.
            if mode == "semantic":
                raise
            log.warning("embedding unavailable; hybrid degraded to keyword arm")
            embedding = None

    rows = await search_repo.hybrid(
        conn,
        user_id=user_id,
        query=query,
        embedding=embedding,
        match_count=match_count or settings.retrieval_k,
        candidate_count=settings.candidate_k,
        vector_weight=settings.vector_weight,
        keyword_weight=settings.keyword_weight,
        rrf_k=settings.rrf_k,
        collection_id=filters.collection_id,
        document_ids=filters.document_ids,
        created_after=filters.created_after,
        created_before=filters.created_before,
        min_similarity=settings.min_similarity,
    )

    hits = [row_to_hit(row) for row in rows]
    hits = dedupe(hits)
    hits = limit_per_document(hits)
    hits = hits[: (match_count or settings.retrieval_k)]

    vector_count = sum(1 for h in hits if h.vector_rank)
    keyword_count = sum(1 for h in hits if h.keyword_rank)

    return RetrievalResult(
        hits=hits,
        sources=to_sources(hits),
        vector_candidates=vector_count,
        keyword_candidates=keyword_count,
    )
