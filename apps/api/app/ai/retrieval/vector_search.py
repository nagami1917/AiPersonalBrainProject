"""The vector arm of retrieval, and the `Hit` vocabulary every arm produces.

Search itself is SQL: `public.hybrid_search` owns the HNSW scan, the cosine
similarity gate, and the fusion. Doing the scan in Python would mean pulling the
whole corpus across the wire, so this module's job is narrower and more useful:

  * `Hit` — the one shape all retrieval modes return, so downstream code
    (diversification, sources, grounding) never branches on mode;
  * `row_to_hit` — the row → Hit mapping, including which table the source id
    came from;
  * `vector()` — the vector-only call, i.e. `candidate_k` neighbours ranked by
    cosine similarity with the absolute relevance floor applied.

Why the floor matters: an approximate index returns k neighbours for *any*
query vector, including nonsense. Rank alone can never express "unrelated", so
similarity is what separates a real match from the nearest irrelevant row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import asyncpg

from app.repositories import search as search_repo

SourceKind = Literal["note", "document"]


@dataclass
class Hit:
    chunk_id: str
    source_kind: SourceKind
    source_id: str  # note_id or document_id, whichever is set
    source_title: str
    content: str
    heading_path: str | None
    page_number: int | None
    char_start: int | None
    char_end: int | None
    vector_rank: int | None
    keyword_rank: int | None
    fused_score: float

    @property
    def matched_arm(self) -> str:
        """Which arm(s) surfaced this row — shown in the UI's match explanation."""
        if self.vector_rank and self.keyword_rank:
            return "both"
        return "vector" if self.vector_rank else "keyword"


def row_to_hit(row: Any) -> Hit:
    record = dict(row)
    kind = record["source_kind"]
    source_id = record["note_id"] if kind == "note" else record["document_id"]
    return Hit(
        chunk_id=str(record["chunk_id"]),
        source_kind=kind,
        source_id=str(source_id),
        source_title=record["source_title"] or "Untitled",
        content=record["content"],
        heading_path=record.get("heading_path"),
        page_number=record.get("page_number"),
        char_start=record.get("char_start"),
        char_end=record.get("char_end"),
        vector_rank=record.get("vector_rank"),
        keyword_rank=record.get("keyword_rank"),
        fused_score=float(record.get("fused_score") or 0.0),
    )


@dataclass(frozen=True)
class SearchFilters:
    """Filters shared by every mode, applied in SQL so all arms see one candidate set."""

    collection_id: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    source_ids: list[str] | None = None
    kind: str | None = None


async def vector(
    conn: asyncpg.Connection,
    *,
    workspace_id: str,
    query: str,
    embedding: list[float],
    match_count: int,
    candidate_count: int,
    min_similarity: float,
    filters: SearchFilters | None = None,
) -> list[Hit]:
    """Vector-only retrieval.

    Implemented as `hybrid_search` with the keyword weight at zero rather than a
    second SQL function: the candidate CTE, the filters, the `status='ready'`
    gate and the parent joins are exactly the same, and duplicating them would
    create two places for a tenancy fix to be forgotten.
    """
    filters = filters or SearchFilters()
    rows = await search_repo.hybrid(
        conn,
        workspace_id=workspace_id,
        query=query,
        embedding=embedding,
        match_count=match_count,
        candidate_count=candidate_count,
        vector_weight=1.0,
        keyword_weight=0.0,
        rrf_k=60,
        min_similarity=min_similarity,
        collection_id=filters.collection_id,
        created_after=filters.created_after,
        created_before=filters.created_before,
        source_ids=filters.source_ids,
        kind=filters.kind,
    )
    return [row_to_hit(row) for row in rows]
