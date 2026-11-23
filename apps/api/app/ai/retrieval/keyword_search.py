"""The keyword arm: exact names, phrases, and technical terms.

Vector search is bad at the things a personal knowledge base is full of —
error codes, function names, "the Fly secrets command", a person's surname. Those
need literal matching, so this arm uses Postgres full-text search
(`websearch_to_tsquery` + `ts_rank_cd`), which also gives the user familiar query
syntax for free: quoted phrases, `or`, and `-exclusion`.

Like the vector arm, the scan is in SQL for the same reason: filters, tenancy and
the `status='ready'` gate must be identical across modes.
"""

from __future__ import annotations

import asyncpg

from app.ai.retrieval.vector_search import Hit, SearchFilters, row_to_hit
from app.repositories import search as search_repo


async def keyword(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    query: str,
    match_count: int,
    candidate_count: int,
    filters: SearchFilters | None = None,
) -> list[Hit]:
    """Keyword-only retrieval.

    Expressed as `hybrid_search` with no embedding supplied and the vector weight
    at zero — see `vector_search.vector` for why the arms share one SQL entry
    point. No embedding is computed at all in this mode, so a question containing
    a rare exact term costs one database round trip and no model call.
    """
    filters = filters or SearchFilters()
    rows = await search_repo.hybrid(
        conn,
        workspace_id=workspace_id,
        query=query,
        embedding=None,
        match_count=match_count,
        candidate_count=candidate_count,
        vector_weight=0.0,
        keyword_weight=1.0,
        rrf_k=60,
        min_similarity=0.0,
        collection_id=filters.collection_id,
        created_after=filters.created_after,
        created_before=filters.created_before,
        source_ids=filters.source_ids,
        kind=filters.kind,
        tag=filters.tag,
    )
    return [row_to_hit(row) for row in rows]
