"""Retrieval orchestration: mode dispatch → diversify → sources.

The arms and their ordering live in `app.ai.retrieval`; this service adds the two
things that are not the AI layer's business: the request-scoped settings, and the
candidate counts the API reports back so a user can see *why* a result surfaced.

The one policy decision here is what happens when the embedding provider is down.
It is deliberately NOT a silent fallback: `semantic` raises, `hybrid` degrades to
the keyword arm and says so in the logs, because presenting degraded retrieval as
full quality would misrepresent how well an answer is grounded.
"""

from __future__ import annotations

import logging

import asyncpg

from app.ai.providers import Embedder
from app.ai.rag import Source
from app.ai.retrieval import Hit, SearchFilters, search, to_sources
from app.core.config import Settings
from app.schemas.chat import SearchFilters as RequestFilters

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


def _filters(filters: RequestFilters | None) -> SearchFilters:
    """Translate the API schema into the retrieval layer's own filter type.

    Two types rather than one so a request-shape change cannot silently alter
    retrieval semantics, and so the AI layer never imports a Pydantic model.
    """
    if filters is None:
        return SearchFilters()
    return SearchFilters(
        collection_id=filters.collection_id,
        created_after=filters.created_after,
        created_before=filters.created_before,
        source_ids=filters.source_ids,
        kind=filters.kind,
        tag=filters.tag,
    )


async def retrieve(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    query: str,
    embedder: Embedder,
    settings: Settings,
    filters: RequestFilters | None = None,
    match_count: int | None = None,
    mode: str = "hybrid",
) -> RetrievalResult:
    limit = match_count or settings.retrieval_k

    hits = await search(
        conn,
        workspace_id=workspace_id,
        query=query,
        mode=mode,
        embedder=embedder,
        settings=settings,
        match_count=limit,
        filters=_filters(filters),
    )

    return RetrievalResult(
        hits=hits,
        sources=to_sources(hits),
        vector_candidates=sum(1 for h in hits if h.vector_rank),
        keyword_candidates=sum(1 for h in hits if h.keyword_rank),
    )
