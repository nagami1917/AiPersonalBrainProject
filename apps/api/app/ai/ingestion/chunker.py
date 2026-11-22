"""Structure-aware chunking.

Deterministic by construction (invariant 5): the same text plus the same
parameters yields the same chunk set, so reprocessing a document is idempotent
and golden-file tests are meaningful.

Boundary preference: heading → paragraph → sentence → word. A chunk may only
end mid-sentence when no larger unit fits, which keeps citations quotable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Pinned by the design's chunking strategy: ~700-token chunks with ~100 tokens of
# overlap. MAX_TOKENS is a ceiling, not a second target — a paragraph that would
# push a chunk past it starts a new one. MIN_TOKENS avoids a one-sentence stub at
# the end of a document by merging it into its predecessor.
#
# These are starting values, not laws. They live in one place so an eval run can
# change them and re-measure retrieval quality, which is the only reason to
# change them at all.
TARGET_TOKENS = 700
MAX_TOKENS = 900
OVERLAP_TOKENS = 100
MIN_TOKENS = 150

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_WS = re.compile(r"[ \t]+")


@dataclass
class TextBlock:
    """A paragraph-level unit plus the heading path it sits under."""

    text: str
    heading_path: str | None
    page_number: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass
class Chunk:
    chunk_index: int
    content: str
    token_count: int
    heading_path: str | None
    char_start: int | None = None
    char_end: int | None = None
    page_number: int | None = None
    page_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def approx_tokens(text: str) -> int:
    """Cheap, dependency-free estimate.

    Deliberately not an exact tokenizer: chunk sizes are a quality heuristic,
    not a billing boundary, and a deterministic estimator keeps tests stable
    across tokenizer versions.
    """
    return max(1, round(len(text) / 4))


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]
    return parts or [text.strip()]


def _split_words(text: str) -> list[str]:
    return text.split()


def normalize_text(raw: str) -> str:
    """Collapse intra-line whitespace, keep paragraph breaks.

    Lowercasing is intentionally NOT done: it damages embeddings and would
    corrupt quoted citations.
    """
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.replace("\u2019", "'").replace("\u2018", "'")
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')
    raw = _WS.sub(" ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def split_into_blocks(
    text: str, page_map: list[tuple[int, int, int]] | None = None
) -> list[TextBlock]:
    """Split normalized text into paragraph blocks tagged with heading path.

    page_map: [(char_offset_start, char_offset_end, page_number)] from PDF
    extraction, used to attach page numbers to citation offsets.
    """
    blocks: list[TextBlock] = []
    heading_stack: list[tuple[int, str]] = []
    cursor = 0

    for paragraph in re.split(r"\n\s*\n", text):
        if not paragraph.strip():
            cursor += len(paragraph) + 1
            continue

        start = text.find(paragraph, cursor)
        if start < 0:
            start = cursor
        end = start + len(paragraph)
        cursor = end

        heading_match = _HEADING.match(paragraph.strip())
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_stack = [h for h in heading_stack if h[0] < level]
            heading_stack.append((level, title))
            continue

        page_number, page_end = _pages_for(start, end, page_map)
        blocks.append(
            TextBlock(
                text=paragraph.strip(),
                heading_path=" > ".join(h[1] for h in heading_stack) or None,
                page_number=page_number,
                page_end=page_end,
                char_start=start,
                char_end=end,
            )
        )
    return blocks


def _pages_for(
    start: int, end: int, page_map: list[tuple[int, int, int]] | None
) -> tuple[int | None, int | None]:
    if not page_map:
        return None, None
    pages = [p for (s, e, p) in page_map if s < end and e > start]
    if not pages:
        return None, None
    return min(pages), max(pages)


def _fit_block(block: TextBlock) -> list[tuple[str, int]]:
    """Break an oversized block into units that individually fit MAX_TOKENS."""
    if approx_tokens(block.text) <= MAX_TOKENS:
        return [(block.text, approx_tokens(block.text))]

    units: list[tuple[str, int]] = []
    for sentence in _split_sentences(block.text):
        tokens = approx_tokens(sentence)
        if tokens <= MAX_TOKENS:
            units.append((sentence, tokens))
            continue
        # Last resort: hard word-boundary split for a single monstrous sentence.
        current: list[str] = []
        current_tokens = 0
        for word in _split_words(sentence):
            wt = approx_tokens(word)
            if current_tokens + wt > MAX_TOKENS and current:
                units.append((" ".join(current), current_tokens))
                current, current_tokens = [], 0
            current.append(word)
            current_tokens += wt
        if current:
            units.append((" ".join(current), current_tokens))
    return units


def _tail_overlap(text: str, tokens: int = OVERLAP_TOKENS) -> str:
    """Last ~`tokens` worth of text, snapped to a sentence boundary.

    Two hard rules, both learned the hard way:
      * the overlap may never exceed the requested budget — a paragraph with no
        sentence punctuation (a list, a table dump) must not return itself;
      * if a single trailing sentence is larger than the budget, cut it at a
        word boundary instead of giving up and returning everything.
    """
    budget_chars = tokens * 4

    sentences = _split_sentences(text)
    kept: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        t = approx_tokens(sentence)
        if total + t > tokens:
            if not kept:
                # Single oversized sentence: take the tail of it on a word edge.
                tail = sentence[-budget_chars:]
                space = tail.find(" ")
                if space > 0:
                    tail = tail[space + 1 :]
                return tail.strip()
            break
        kept.insert(0, sentence)
        total += t
    return " ".join(kept).strip()


def chunk_blocks(blocks: list[TextBlock]) -> list[Chunk]:
    """Greedily pack blocks into chunks near TARGET_TOKENS."""
    chunks: list[Chunk] = []
    buffer: list[TextBlock] = []
    buffer_tokens = 0
    chunk_index = 0

    def flush() -> None:
        nonlocal buffer, buffer_tokens, chunk_index
        if not buffer:
            return
        content = "\n\n".join(b.text for b in buffer).strip()
        if not content:
            buffer, buffer_tokens = [], 0
            return
        tokens = approx_tokens(content)
        heading = next((b.heading_path for b in buffer if b.heading_path), None)
        chunks.append(
            Chunk(
                chunk_index=chunk_index,
                content=content,
                token_count=tokens,
                heading_path=heading,
                char_start=min(b.char_start for b in buffer if b.char_start is not None)
                if any(b.char_start is not None for b in buffer)
                else None,
                char_end=max(b.char_end for b in buffer if b.char_end is not None)
                if any(b.char_end is not None for b in buffer)
                else None,
                page_number=min(b.page_number for b in buffer if b.page_number is not None)
                if any(b.page_number is not None for b in buffer)
                else None,
                page_end=max(b.page_end for b in buffer if b.page_end is not None)
                if any(b.page_end is not None for b in buffer)
                else None,
            )
        )
        chunk_index += 1
        buffer, buffer_tokens = [], 0

    for block in blocks:
        for unit_text, unit_tokens in _fit_block(block):
            # A unit could still be a full block that fits, so re-check size
            # before packing: never let a chunk exceed MAX_TOKENS.
            if (
                buffer
                and buffer_tokens + unit_tokens > MAX_TOKENS
                or buffer_tokens + unit_tokens > TARGET_TOKENS
                and buffer_tokens >= MIN_TOKENS
            ):
                overlap = _tail_overlap("\n\n".join(b.text for b in buffer))
                flush()
                if overlap:
                    buffer = [
                        TextBlock(
                            text=overlap,
                            heading_path=block.heading_path,
                            page_number=block.page_number,
                            page_end=block.page_end,
                        )
                    ]
                    buffer_tokens = approx_tokens(overlap)
            buffer.append(
                TextBlock(
                    text=unit_text,
                    heading_path=block.heading_path,
                    page_number=block.page_number,
                    page_end=block.page_end,
                    char_start=block.char_start,
                    char_end=block.char_end,
                )
            )
            buffer_tokens += unit_tokens

    flush()

    # Merge a trailing fragment too small to stand alone into its predecessor.
    if len(chunks) > 1 and chunks[-1].token_count < MIN_TOKENS:
        tail = chunks.pop()
        prev = chunks[-1]
        merged = f"{prev.content}\n\n{tail.content}".strip()
        chunks[-1] = Chunk(
            chunk_index=prev.chunk_index,
            content=merged,
            token_count=approx_tokens(merged),
            heading_path=prev.heading_path or tail.heading_path,
            char_start=prev.char_start,
            char_end=tail.char_end if tail.char_end is not None else prev.char_end,
            page_number=prev.page_number,
            page_end=tail.page_end or prev.page_end,
        )

    return chunks


def chunk_text(text: str, page_map: list[tuple[int, int, int]] | None = None) -> list[Chunk]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    return chunk_blocks(split_into_blocks(normalized, page_map))
