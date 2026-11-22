"""Retrieval data access — a thin wrapper over public.hybrid_search."""

from __future__ import annotations

from typing import Any

import asyncpg

HYBRID_SQL = """
select * from public.hybrid_search(
  p_user_id        => $1,
  p_query          => $2,
  p_embedding      => $3::vector,
  p_match_count    => $4,
  p_candidate_count => $5,
  p_vector_weight  => $6,
  p_keyword_weight => $7,
  p_rrf_k          => $8,
  p_collection_id  => $9,
  p_document_ids   => $10,
  p_created_after  => $11,
  p_created_before => $12,
  p_min_similarity => $13
)
"""


async def hybrid(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    query: str,
    embedding: list[float] | None,
    match_count: int,
    candidate_count: int,
    vector_weight: float,
    keyword_weight: float,
    rrf_k: int,
    collection_id: str | None = None,
    document_ids: list[str] | None = None,
    created_after: Any = None,
    created_before: Any = None,
    min_similarity: float = 0.0,
) -> list[asyncpg.Record]:
    vector_literal = "[" + ",".join(f"{v:.7f}" for v in embedding) + "]" if embedding else None
    rows = await conn.fetch(
        HYBRID_SQL,
        user_id,
        query,
        vector_literal,
        match_count,
        candidate_count,
        vector_weight,
        keyword_weight,
        rrf_k,
        collection_id,
        document_ids,
        created_after,
        created_before,
        min_similarity,
    )
    return list(rows)
