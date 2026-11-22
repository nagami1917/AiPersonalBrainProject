"""Ingestion: bytes (or text) in, embedded chunks out.

The three modules split along the axis of "what can be tested without a
database":

  * `extractors` — format → text + a page map. Pure functions over bytes.
  * `chunker`    — text → ordered chunks with overlap. Pure functions.
  * `processor`  — the ordered pipeline that composes them (§4.1 steps 6–8).

Persistence — stage updates, version guards, the transaction that writes chunks
and flips status — stays in `app.workers.ingest`, because it is a database
concern and belongs with the queue that drives it.
"""

from __future__ import annotations

from app.ai.ingestion.chunker import (
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    Chunk,
    approx_tokens,
    chunk_text,
    normalize_text,
)
from app.ai.ingestion.extractors import (
    ExtractionError,
    ExtractionResult,
    extract,
    extractor_for,
    looks_like_scan,
)
from app.ai.ingestion.processor import (
    EmptySourceError,
    PreparedChunk,
    clean,
    embed,
    prepare_chunks,
    split,
)

__all__ = [
    "OVERLAP_TOKENS",
    "TARGET_TOKENS",
    "Chunk",
    "EmptySourceError",
    "ExtractionError",
    "ExtractionResult",
    "PreparedChunk",
    "approx_tokens",
    "clean",
    "chunk_text",
    "embed",
    "extract",
    "extractor_for",
    "looks_like_scan",
    "normalize_text",
    "prepare_chunks",
    "split",
]
