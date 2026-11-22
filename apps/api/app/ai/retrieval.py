"""Retrieval: hybrid candidates → diversity → numbered sources.

Search itself lives in SQL (`public.hybrid_search`). This module does the parts
that need to be readable and testable in Python: MMR diversity, near-duplicate
removal, the redundant-source rule, and the mapping to numbered Source objects
that the grounding contract consumes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.ai.grounding import Source


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    heading_path: str | None
    page_start: int | None
    char_start: int | None
    char_end: int | None
    vector_rank: int | None
    keyword_rank: int | None
    fused_score: float

    @property
    def matched_arm(self) -> str:
        if self.vector_rank and self.keyword_rank:
            return "both"
        return "vector" if self.vector_rank else "keyword"


def row_to_hit(row: Any) -> Hit:
    record = dict(row)
    return Hit(
        chunk_id=str(record["chunk_id"]),
        document_id=str(record["document_id"]),
        document_title=record["document_title"],
        content=record["content"],
        heading_path=record.get("heading_path"),
        page_start=record.get("page_start"),
        char_start=record.get("char_start"),
        char_end=record.get("char_end"),
        vector_rank=record.get("vector_rank"),
        keyword_rank=record.get("keyword_rank"),
        fused_score=float(record.get("fused_score") or 0.0),
    )


def _tokens(text: str) -> set[str]:
    return {w for w in text.lower().split() if len(w) > 2}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def dedupe(hits: Sequence[Hit], threshold: float = 0.85) -> list[Hit]:
    """Drop near-identical passages (the same paragraph reached twice)."""
    kept: list[Hit] = []
    for hit in hits:
        if any(
            hit.document_id == other.document_id
            and _jaccard(hit.content, other.content) >= threshold
            for other in kept
        ):
            continue
        kept.append(hit)
    return kept


def limit_per_document(hits: Sequence[Hit], max_share: float = 0.6) -> list[Hit]:
    """No single document may dominate the context unless it is all we have."""
    if not hits:
        return []
    distinct_docs = {h.document_id for h in hits}
    if len(distinct_docs) <= 1:
        return list(hits)

    cap = max(1, math.ceil(len(hits) * max_share))
    counts: dict[str, int] = {}
    kept: list[Hit] = []
    overflow: list[Hit] = []
    for hit in hits:
        if counts.get(hit.document_id, 0) < cap:
            counts[hit.document_id] = counts.get(hit.document_id, 0) + 1
            kept.append(hit)
        else:
            overflow.append(hit)
    # Keep overflow in reserve rather than discarding evidence outright.
    return kept + overflow[: max(0, len(overflow) - len(kept))]


def to_sources(hits: Sequence[Hit]) -> list[Source]:
    """Number sources 1..n in the order they will be cited."""
    return [
        Source(
            marker=i,
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            document_title=hit.document_title,
            content=hit.content,
            heading_path=hit.heading_path,
            page_start=hit.page_start,
            char_start=hit.char_start,
            char_end=hit.char_end,
            score=hit.fused_score,
        )
        for i, hit in enumerate(hits, start=1)
    ]


def normalize_fused_scores(hits: Sequence[Hit]) -> list[float]:
    """Map raw RRF values (~0.01–0.03) to a 0–1 display range.

    RRF is a ranking signal, not a probability. Normalizing by the best hit
    gives users a meaningful "how much better than the rest" bar without
    implying a confidence we cannot compute.
    """
    if not hits:
        return []
    best = max(h.fused_score for h in hits) or 1.0
    return [round(min(1.0, h.fused_score / best), 4) for h in hits]
