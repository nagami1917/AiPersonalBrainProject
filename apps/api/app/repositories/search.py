"""Retrieval data access — a thin wrapper over public.hybrid_search.

The SQL function resolves the note/document parent itself and returns
`source_kind`, so callers never need to know which table a hit came from until
they render a citation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

HYBRID_SQL = """
select * from public.hybrid_search(
  p_workspace_id   => $1,
  p_query          => $2,
  p_embedding      => $3::vector,
  p_match_count    => $4,
  p_candidate_count => $5,
  p_vector_weight  => $6,
  p_keyword_weight => $7,
  p_rrf_k          => $8,
  p_collection_id  => $9,
  p_created_after  => $10,
  p_created_before => $11,
  p_min_similarity => $12,
  p_source_ids     => $13,
  p_kind           => $14
)
"""


async def hybrid(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    query: str,
    embedding: list[float] | None,
    match_count: int,
    candidate_count: int,
    vector_weight: float,
    keyword_weight: float,
    rrf_k: int,
    min_similarity: float,
    collection_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    source_ids: list[str] | None = None,
    kind: str | None = None,
) -> list[asyncpg.Record]:
    vector_literal = "[" + ",".join(f"{v:.7f}" for v in embedding) + "]" if embedding else None
    rows: list[Any] = await conn.fetch(
        HYBRID_SQL,
        workspace_id,
        query,
        vector_literal,
        match_count,
        candidate_count,
        vector_weight,
        keyword_weight,
        rrf_k,
        collection_id,
        created_after,
        created_before,
        min_similarity,
        source_ids,
        kind,
    )
    return list(rows)
