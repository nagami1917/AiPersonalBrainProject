"""Retrieval: two arms, one candidate shape, then reordering.

Design §4.3 splits retrieval into `vector_search`, `keyword_search` and
`reranker`, and the recommendation is to ship vector search first and add the
keyword arm and a reranker as quality demands. This package is already at the
"hybrid" step: both arms exist, fused by Reciprocal Rank Fusion in SQL.

Where the work actually happens, and why:

  * Arms (`vector_search`, `keyword_search`) — the scan is in Postgres, because
    it has the index and the vectors. These modules own the *policy*: which
    weight, how many candidates, what the similarity floor is.
  * `reranker` — everything after candidates arrive: dedupe, diversity, scaling.
    No model call, deliberately (see the module docstring).
  * `to_sources` — the numbering that becomes citation markers [1]..[n].

Mode selection lives here so callers never assemble arms by hand.
"""

from __future__ import annotations

from collections.abc import Sequence

import asyncpg

from app.ai.providers import Embedder
from app.ai.rag.context_builder import Source
from app.ai.retrieval.keyword_search import keyword
from app.ai.retrieval.reranker import (
    dedupe,
    limit_per_source,
    normalize_fused_scores,
    order,
)
from app.ai.retrieval.vector_search import Hit, SearchFilters, row_to_hit, vector
from app.core.config import Settings
from app.core.errors import ProviderUnavailable
from app.repositories import search as search_repo

__all__ = [
    "Hit",
    "SearchFilters",
    "dedupe",
    "hybrid",
    "keyword",
    "limit_per_source",
    "normalize_fused_scores",
    "order",
    "row_to_hit",
    "to_sources",
    "vector",
]


async def hybrid(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    query: str,
    embedding: list[float] | None,
    settings: Settings,
    match_count: int,
    filters: SearchFilters | None = None,
) -> list[Hit]:
    """Both arms, fused by RRF in SQL, then reordered in Python.

    `embedding=None` degrades to the keyword arm, which is what the caller
    decides to do when the embedding provider is down. That decision is never
    made silently here: presenting degraded retrieval as full quality would
    misrepresent how well the answer is grounded.
    """
    filters = filters or SearchFilters()
    rows = await search_repo.hybrid(
        conn,
        workspace_id=workspace_id,
        query=query,
        embedding=embedding,
        match_count=match_count,
        candidate_count=settings.candidate_k,
        vector_weight=settings.vector_weight,
        keyword_weight=settings.keyword_weight,
        rrf_k=settings.rrf_k,
        min_similarity=settings.min_similarity,
        collection_id=filters.collection_id,
        created_after=filters.created_after,
        created_before=filters.created_before,
        source_ids=filters.source_ids,
        kind=filters.kind,
    )
    return order([row_to_hit(row) for row in rows], limit=match_count)


async def search(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    query: str,
    mode: str,
    embedder: Embedder,
    settings: Settings,
    match_count: int,
    filters: SearchFilters | None = None,
) -> list[Hit]:
    """Dispatch on mode: `semantic` | `keyword` | `hybrid` (default)."""
    filters = filters or SearchFilters()

    if mode == "keyword":
        return await keyword(
            conn,
            workspace_id=workspace_id,
            query=query,
            match_count=match_count,
            candidate_count=settings.candidate_k,
            filters=filters,
        )

    embedding: list[float] | None = None
    if mode != "keyword":
        try:
            vectors = await embedder.embed([query])
            embedding = vectors[0] if vectors else None
        except ProviderUnavailable:
            # Deliberately NOT a silent keyword-only fallback for chat: the
            # caller decides, because presenting degraded retrieval as full
            # quality would misrepresent how well the answer is grounded.
            if mode == "semantic":
                raise
            embedding = None

    if mode == "semantic" and embedding is not None:
        return await vector(
            conn,
            workspace_id=workspace_id,
            query=query,
            embedding=embedding,
            match_count=match_count,
            candidate_count=settings.candidate_k,
            min_similarity=settings.min_similarity,
            filters=filters,
        )

    return await hybrid(
        conn,
        workspace_id=workspace_id,
        query=query,
        embedding=embedding,
        settings=settings,
        match_count=match_count,
        filters=filters,
    )


def to_sources(hits: Sequence[Hit]) -> list[Source]:
    """Number sources 1..n in the order they will be cited."""
    return [
        Source(
            marker=i,
            chunk_id=hit.chunk_id,
            document_id=hit.source_id,
            document_title=hit.source_title,
            content=hit.content,
            heading_path=hit.heading_path,
            page_number=hit.page_number,
            char_start=hit.char_start,
            char_end=hit.char_end,
            score=hit.fused_score,
            source_kind=hit.source_kind,
            source_filename=hit.source_filename,
        )
        for i, hit in enumerate(hits, start=1)
    ]
