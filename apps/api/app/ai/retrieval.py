"""Retrieval: hybrid candidates → diversity → numbered sources.

Search itself lives in SQL (`public.hybrid_search`). This module does the parts
that need to be readable and testable in Python: near-duplicate removal, the
per-source diversity cap, and the mapping to numbered Source objects that the
grounding contract consumes.

New with the workspace model: every hit carries `source_kind`, so a citation can
say "note" or "document" without a second query.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.ai.grounding import Source

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
            hit.source_kind == other.source_kind
            and hit.source_id == other.source_id
            and _jaccard(hit.content, other.content) >= threshold
            for other in kept
        ):
            continue
        kept.append(hit)
    return kept


def limit_per_source(hits: Sequence[Hit], max_share: float = 0.6) -> list[Hit]:
    """No single note/document may dominate the context unless it is all we have.

    Applied across the union of both source kinds, keyed on (kind, id), because a
    long document crowding out a better note is the same failure either way.
    """
    if not hits:
        return []
    distinct = {(h.source_kind, h.source_id) for h in hits}
    if len(distinct) <= 1:
        return list(hits)

    cap = max(1, math.ceil(len(hits) * max_share))
    counts: dict[tuple[str, str], int] = {}
    kept: list[Hit] = []
    overflow: list[Hit] = []
    for hit in hits:
        key = (hit.source_kind, hit.source_id)
        if counts.get(key, 0) < cap:
            counts[key] = counts.get(key, 0) + 1
            kept.append(hit)
        else:
            overflow.append(hit)
    return kept + overflow[: max(0, len(overflow) - len(kept))]


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
        )
        for i, hit in enumerate(hits, start=1)
    ]


def normalize_fused_scores(hits: Sequence[Hit]) -> list[float]:
    """Map raw RRF values (~0.01–0.03) to a 0–1 display range.

    RRF is a ranking signal, not a probability. Normalizing by the best hit gives
    users a meaningful "how much better than the rest" bar without implying a
    confidence we cannot compute.
    """
    if not hits:
        return []
    best = max(h.fused_score for h in hits) or 1.0
    return [round(min(1.0, h.fused_score / best), 4) for h in hits]
