"""The model's output is UNTRUSTED INPUT. This module is where that is enforced.

The contract in RAG-PIPELINE.md §4, executable:

  * the ungrounded decision is made BEFORE the model is called (`should_refuse`);
  * markers the model invents are counted and stripped;
  * only markers that map to real retrieved chunks become persisted citations;
  * an answer with no surviving citation is replaced by the refusal template,
    however confident it reads.

Nothing here calls a network. That is the point: the guarantee is testable
without a model, which is the only way to trust it. The model call itself lives
in `rag/generator.py`-adjacent orchestration (`services/chat_service.py`); this
file decides what the result is *allowed* to claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ai.rag.context_builder import Source

MARKER_RE = re.compile(r"\[(\d{1,3})\]")

REFUSAL_TEMPLATE = (
    "I couldn't find anything in your knowledge base about that. "
    "Try rephrasing, or check that the relevant document finished processing."
)


@dataclass
class Citation:
    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    quote: str
    page: int | None
    heading_path: str | None
    score: float
    source_kind: str = "document"
    source_filename: str | None = None


@dataclass
class GroundedAnswer:
    content: str
    grounded: bool
    citations: list[Citation] = field(default_factory=list)
    fabricated_markers: list[int] = field(default_factory=list)
    stripped_content: str = ""

    @property
    def is_refusal(self) -> bool:
        return not self.grounded


def should_refuse(sources: list[Source], score_floor: float) -> bool:
    """The ungrounded decision, made BEFORE spending a model call.

    Retrieval found nothing, or nothing above the floor → refuse deterministically.
    A confident invention is the exact failure this product exists to prevent.
    """
    if not sources:
        return True
    return max(s.score for s in sources) < score_floor


def resolve_citations(
    model_output: str,
    sources: list[Source],
    *,
    quote_chars: int = 240,
) -> GroundedAnswer:
    """Resolve markers to real sources; drop everything fabricated."""
    by_marker = {s.marker: s for s in sources}
    seen: set[int] = set()
    fabricated: list[int] = []
    citations: list[Citation] = []

    for match in MARKER_RE.finditer(model_output):
        marker = int(match.group(1))
        if marker in seen or marker in fabricated:
            continue
        source = by_marker.get(marker)
        if source is None:
            fabricated.append(marker)
            continue
        seen.add(marker)
        citations.append(
            Citation(
                marker=marker,
                chunk_id=source.chunk_id,
                document_id=source.document_id,
                document_title=source.document_title,
                quote=_quote_from(source.content, quote_chars),
                page=source.page_number,
                heading_path=source.heading_path,
                score=round(source.score, 4),
                source_kind=source.source_kind,
                source_filename=source.source_filename,
            )
        )

    stripped = model_output
    for marker in fabricated:
        stripped = re.sub(rf"\[\s*{marker}\s*\]", "", stripped)
    # Tidy artifacts left by marker removal
    stripped = re.sub(r"[ \t]{2,}", " ", stripped)
    stripped = re.sub(r" +([.,;:])", r"\1", stripped).strip()

    citations.sort(key=lambda c: c.marker)
    grounded = bool(citations)

    if not grounded:
        # An answer with no surviving citation is not evidence of grounding,
        # even if it reads confidently.
        return GroundedAnswer(
            content=REFUSAL_TEMPLATE,
            grounded=False,
            citations=[],
            fabricated_markers=fabricated,
            stripped_content=stripped,
        )

    return GroundedAnswer(
        content=stripped,
        grounded=True,
        citations=citations,
        fabricated_markers=fabricated,
        stripped_content=stripped,
    )


def _quote_from(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content.strip()
    cut = content[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("\n"))
    if boundary > limit // 2:
        cut = cut[: boundary + 1]
    return cut.strip() + "…"
