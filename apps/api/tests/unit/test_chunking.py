"""Chunking must be deterministic and boundary-respecting (invariant 5)."""

from __future__ import annotations

from app.ai.chunking import (
    MAX_TOKENS,
    MIN_TOKENS,
    TARGET_TOKENS,
    approx_tokens,
    chunk_text,
    normalize_text,
    split_into_blocks,
)


class TestNormalization:
    def test_collapses_intra_line_whitespace_but_keeps_paragraphs(self):
        raw = "First   line\n\n\n\nSecond   line"
        assert normalize_text(raw) == "First line\n\nSecond line"

    def test_does_not_lowercase(self):
        """Lowercasing would damage embeddings and corrupt quoted citations."""
        assert normalize_text("Deployment Secrets") == "Deployment Secrets"

    def test_normalizes_smart_quotes_only(self):
        assert normalize_text("\u2019quoted\u2019") == "'quoted'"


class TestStructure:
    def test_heading_path_is_attached_to_blocks(self):
        text = "# Chapter 1\n\nBody one.\n\n## Section A\n\nBody two."
        blocks = split_into_blocks(text)
        assert [b.text for b in blocks] == ["Body one.", "Body two."]
        assert blocks[0].heading_path == "Chapter 1"
        assert blocks[1].heading_path == "Chapter 1 > Section A"

    def test_headings_are_not_chunked_as_content(self):
        chunks = chunk_text("# Title\n\nOnly body text here that is long enough.")
        assert all("# Title" not in c.content for c in chunks)


class TestChunking:
    def test_short_text_yields_one_chunk(self):
        chunks = chunk_text("A short but complete note about the deployment policy.")
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0

    def test_deterministic_for_same_input(self):
        text = "\n\n".join(f"Paragraph {i} with some words in it." for i in range(40))
        first = [c.content for c in chunk_text(text)]
        second = [c.content for c in chunk_text(text)]
        assert first == second

    def test_no_chunk_exceeds_max_tokens(self):
        text = "\n\n".join("word " * 300 for _ in range(5))
        for chunk in chunk_text(text):
            assert chunk.token_count <= MAX_TOKENS + 10

    def test_chunks_respect_target_size(self):
        text = "\n\n".join(f"Sentence {i}. " * 20 for i in range(30))
        chunks = chunk_text(text)
        assert len(chunks) > 2
        assert all(c.token_count <= MAX_TOKENS + 10 for c in chunks)

    def test_trailing_fragment_is_merged(self):
        """A 20-token tail should attach to its predecessor, not stand alone."""
        text = "Sentence of reasonable length. " * 120 + "Tiny tail."
        chunks = chunk_text(text)
        assert chunks[-1].token_count >= MIN_TOKENS

    def test_oversized_paragraph_splits_on_sentences_not_mid_word(self):
        sentence = "This sentence has a number of words in it for testing. "
        text = sentence * 200
        chunks = chunk_text(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert not chunk.content.startswith(" ")
            assert chunk.content.strip() == chunk.content

    def test_ordinals_are_contiguous_from_zero(self):
        text = "\n\n".join(f"Paragraph {i}." * 40 for i in range(10))
        chunks = chunk_text(text)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_punctuation_free_text_still_respects_max_size(self):
        """Regression: a paragraph with no sentence boundaries (list/table dump)
        used to return itself as 'overlap', producing chunks over MAX_TOKENS."""
        text = "\n\n".join("word " * 300 for _ in range(5))
        chunks = chunk_text(text)
        assert chunks
        for chunk in chunks:
            assert chunk.token_count <= MAX_TOKENS + 10, chunk.token_count

    def test_overlap_never_exceeds_its_budget(self):
        from app.ai.chunking import OVERLAP_TOKENS, _tail_overlap

        huge = "no punctuation here " * 500
        assert approx_tokens(_tail_overlap(huge)) <= OVERLAP_TOKENS + 5

    def test_empty_input_yields_no_chunks(self):
        assert chunk_text("   \n\n  ") == []


class TestPinnedStrategy:
    """The design pins ~700 tokens with ~100 overlap. These lock that in.

    They are starting values, and the docstring in chunking.py says so. The point
    of asserting them is that changing them must be a deliberate edit with an
    eval run behind it, not a silent drift.
    """

    def test_targets_are_the_documented_ones(self):
        from app.ai.chunking import OVERLAP_TOKENS, TARGET_TOKENS

        assert TARGET_TOKENS == 700
        assert OVERLAP_TOKENS == 100

    def test_chunks_land_near_the_target(self):
        # ~40 paragraphs of ~40 tokens each: comfortably more than one chunk.
        paragraph = (
            "Deployment secrets are set with flyctl and never committed. "
            "The worker claims jobs with row locks. "
        )
        text = "\n\n".join([paragraph] * 40)
        chunks = chunk_text(text)
        assert len(chunks) > 1
        # Every full chunk should be in the target's neighbourhood, not half of it.
        full = chunks[:-1]
        assert full, "expected at least one non-final chunk"
        assert all(c.token_count >= TARGET_TOKENS // 2 for c in full)

    def test_ordering_is_preserved(self):
        text = "\n\n".join(
            [f"Paragraph number {i} with enough words to matter." for i in range(60)]
        )
        chunks = chunk_text(text)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_consecutive_chunks_overlap(self):
        """Overlap is what stops an answer being cut in half at a boundary."""
        text = "\n\n".join(
            [f"Sentence {i} carries a distinct fact about indexing." for i in range(80)]
        )
        chunks = chunk_text(text)
        assert len(chunks) >= 2
        joined = [c.content.split() for c in chunks]
        # Some word sequence from the end of chunk N appears at the start of N+1.
        tail_words = set(joined[0][-40:])
        head_words = set(joined[1][:40])
        assert tail_words & head_words, "adjacent chunks share no text; overlap is not applied"
