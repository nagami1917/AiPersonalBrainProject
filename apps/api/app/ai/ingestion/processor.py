"""The ordered ingest pipeline, minus the database.

Design §4.1 gives the pipeline a fixed order. Steps 6–8 (clean and normalize,
split into chunks, generate embeddings) are pure enough to live here on their
own, which is what makes them testable without a worker, a queue, or Postgres.

Steps 1–5 (storage, records, jobs) and 9–12 (persistence, summary, entities,
the completed mark) are deliberately *not* here: they are database and queue
concerns, and pretending otherwise produces a module that is only mockable, not
testable. They live in `app.workers.ingest`, which calls into this module.

Each step returns its output rather than mutating in place, so the worker can
record progress between them (`processing_stage`) without this module knowing
what a stage is.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.ai.ingestion.chunker import Chunk, chunk_text, normalize_text


# Embedding is the only step that needs I/O, so it is injected rather than
# imported. Structural typing keeps this module free of provider knowledge.
class _Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]: ...


class EmptySourceError(Exception):
    """Nothing survived normalization.

    Distinct from "the upload was unreadable": extraction may have produced
    whitespace and page furniture, in which case the honest report is the same
    but the cause is worth separating in logs.
    """


@dataclass(frozen=True)
class PreparedChunk:
    """A chunk plus the vector that will be stored beside it."""

    chunk: Chunk
    embedding: list[float]


def clean(text: str) -> str:
    """§4.1 step 6 — clean and normalize.

    Exposed as its own function because the worker reports it as its own stage,
    and because a normalization change must be reviewable on its own.
    """
    cleaned = normalize_text(text)
    if not cleaned.strip():
        raise EmptySourceError("Normalization produced no usable text.")
    return cleaned


def split(text: str, page_map: list[tuple[int, int, int]] | None = None) -> list[Chunk]:
    """§4.1 step 7 — split into chunks, preserving page and order metadata."""
    chunks = chunk_text(text, page_map)
    if not chunks:
        raise EmptySourceError("No chunks could be produced from this source.")
    return chunks


async def embed(chunks: list[Chunk], embedder: _Embedder) -> list[list[float]]:
    """§4.1 step 8 — generate embeddings, one per chunk, in order.

    A short result is a bug, not a partial success: storing fewer vectors than
    chunks would leave an index that reports ready while silently missing rows.
    """
    if not chunks:
        return []
    vectors = await embedder.embed([chunk.content for chunk in chunks])
    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks; "
            "refusing to store a partial index"
        )
    return vectors


StageCallback = Callable[[str], Awaitable[None]]


async def prepare_chunks(
    text: str,
    embedder: _Embedder,
    *,
    page_map: list[tuple[int, int, int]] | None = None,
    on_stage: StageCallback | None = None,
) -> list[PreparedChunk]:
    """Run steps 6–8 in order and pair each chunk with its vector.

    Ordering is the contract: cleaning before splitting means the chunk text that
    gets embedded and later quoted is already normalized, and embeddings are
    aligned to chunk order, which the version-guarded writer relies on.

    `on_stage` exists so progress reporting can be *honest*: the worker passes a
    callback that records `processing_stage` as each step actually begins. The
    alternative — the worker setting all three stages before calling this — would
    display a progress bar that races to 70% before any work happened, which is
    worse than no progress bar.
    """
    if on_stage is not None:
        await on_stage("normalizing")
    cleaned = clean(text)

    if on_stage is not None:
        await on_stage("chunking")
    chunks = split(cleaned, page_map)

    if on_stage is not None:
        await on_stage("embedding")
    vectors = await embed(chunks, embedder)

    return [PreparedChunk(chunk=c, embedding=v) for c, v in zip(chunks, vectors, strict=True)]
