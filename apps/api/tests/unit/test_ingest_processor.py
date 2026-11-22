"""The ingest pipeline's ordered steps, tested without a worker or a database.

This is the payoff of splitting `processor.py` out of `workers/ingest.py`: the
order of clean → split → embed is now assertable directly, and a future edit that
embeds before cleaning fails here rather than producing quietly worse retrieval.
"""

from __future__ import annotations

import pytest
from app.ai.ingestion import EmptySourceError, clean, embed, prepare_chunks, split

TEXT = (
    "Deployment notes.\n\n"
    "Secrets are set with flyctl and never committed.\n\n"
    "The worker claims jobs under a row lock so two workers never process one row."
)


class _Embedder:
    """Records exactly what it was asked to embed, in order."""

    model = "fake"

    def __init__(self, short_by: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.short_by = short_by

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        self.calls.append(list(texts))
        count = max(0, len(texts) - self.short_by)
        return [[float(i)] * 4 for i in range(count)]


class TestClean:
    def test_it_collapses_whitespace(self):
        assert "a b" in clean("a    b")

    def test_whitespace_only_is_an_error_not_an_empty_chunk_set(self):
        """A distinct exception so the worker can report the real cause."""
        with pytest.raises(EmptySourceError):
            clean("   \n\n\t  ")


class TestSplit:
    def test_empty_text_is_an_error(self):
        with pytest.raises(EmptySourceError):
            split("   ")

    def test_chunks_are_ordered(self):
        chunks = split("\n\n".join(f"Paragraph {i} with several words in it." for i in range(40)))
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_page_map_is_carried_through(self):
        text = "Page one body text."
        chunks = split(text, [(0, len(text), 1)])
        assert chunks[0].page_number == 1


class TestEmbed:
    async def test_vectors_are_requested_in_chunk_order(self):
        chunks = split(
            "\n\n".join(f"Block {i} has enough words to matter here." for i in range(30))
        )
        embedder = _Embedder()
        await embed(chunks, embedder)
        assert embedder.calls[0] == [c.content for c in chunks]

    async def test_a_short_vector_batch_is_refused(self):
        """Storing fewer vectors than chunks would report ready with a hole in the index."""
        chunks = split(
            "\n\n".join(f"Block {i} has enough words to matter here." for i in range(30))
        )
        with pytest.raises(RuntimeError, match="refusing to store a partial index"):
            await embed(chunks, _Embedder(short_by=1))

    async def test_no_chunks_means_no_provider_call(self):
        embedder = _Embedder()
        assert await embed([], embedder) == []
        assert embedder.calls == []


class TestPrepare:
    async def test_cleaning_happens_before_splitting(self):
        """The chunk text is what gets embedded and cited, so it must already be clean."""
        prepared = await prepare_chunks("Messy   text.\n\n\n\nSecond   part.", _Embedder())
        joined = " ".join(item.chunk.content for item in prepared)
        assert "  " not in joined
        assert "Messy text." in joined

    async def test_chunks_and_vectors_stay_paired(self):
        # Comfortably over the 700-token target, so this must split.
        long_text = "\n\n".join([TEXT] * 60)
        prepared = await prepare_chunks(long_text, _Embedder())
        assert len(prepared) > 1
        assert [item.embedding for item in prepared] == [
            [float(i)] * 4 for i in range(len(prepared))
        ]

    async def test_the_order_is_clean_then_split_then_embed(self, monkeypatch):
        """Asserted by recording the sequence, not by inspecting the result."""
        from app.ai.ingestion import processor

        trace: list[str] = []
        real_clean, real_split, real_embed = processor.clean, processor.split, processor.embed

        monkeypatch.setattr(processor, "clean", lambda t: trace.append("clean") or real_clean(t))
        monkeypatch.setattr(
            processor, "split", lambda t, p=None: trace.append("split") or real_split(t, p)
        )

        async def traced_embed(chunks, embedder):  # type: ignore[no-untyped-def]
            trace.append("embed")
            return await real_embed(chunks, embedder)

        monkeypatch.setattr(processor, "embed", traced_embed)
        await processor.prepare_chunks(TEXT, _Embedder())
        assert trace == ["clean", "split", "embed"]


class TestProgressReporting:
    """The stage callback must bracket real work, not fire up front.

    A progress bar that jumps to 70% before any embedding happened is worse than
    no progress bar: it tells the user something that is not true about their
    document.
    """

    async def test_stages_fire_in_order(self):
        seen: list[str] = []

        async def report(stage: str) -> None:
            seen.append(stage)

        await prepare_chunks(TEXT, _Embedder(), on_stage=report)
        assert seen == ["normalizing", "chunking", "embedding"]

    async def test_embedding_stage_fires_after_vectors_are_requested(self):
        """Order alone is not enough; the stage must precede its own work."""
        events: list[str] = []

        class Recording(_Embedder):
            async def embed(self, texts):  # type: ignore[no-untyped-def]
                events.append("embed:bodies")
                return await super().embed(texts)

        async def report(stage: str) -> None:
            events.append(f"stage:{stage}")

        await prepare_chunks(TEXT, Recording(), on_stage=report)
        assert events.index("stage:embedding") < events.index("embed:bodies")

    async def test_a_failing_step_stops_the_stages(self):
        """No stage is reported for work that never started."""
        seen: list[str] = []

        async def report(stage: str) -> None:
            seen.append(stage)

        with pytest.raises(EmptySourceError):
            await prepare_chunks("   ", _Embedder(), on_stage=report)
        assert seen == ["normalizing"]

    async def test_reporter_is_optional(self):
        prepared = await prepare_chunks(TEXT, _Embedder())
        assert prepared
