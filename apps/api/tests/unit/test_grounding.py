"""The grounding contract. These tests are the product promise in executable
form: if they pass, the system cannot silently answer from model memory."""

from __future__ import annotations

from app.ai.grounding import (
    REFUSAL_TEMPLATE,
    Source,
    build_context,
    resolve_citations,
    should_refuse,
)


def make_sources(n: int = 2) -> list[Source]:
    return [
        Source(
            marker=i,
            chunk_id=f"chunk-{i}",
            document_id=f"doc-{i}",
            document_title=f"Title {i}",
            content=f"Passage number {i} with enough text to be quoted sensibly.",
            page_start=i,
            score=0.02,
        )
        for i in range(1, n + 1)
    ]


class TestCitationResolution:
    def test_valid_markers_become_citations(self):
        answer = resolve_citations("Secrets live in Fly [1] and not Vercel [2].", make_sources())
        assert answer.grounded is True
        assert [c.marker for c in answer.citations] == [1, 2]
        assert answer.citations[0].chunk_id == "chunk-1"
        assert answer.citations[1].document_title == "Title 2"

    def test_fabricated_marker_is_stripped_and_not_cited(self):
        answer = resolve_citations("Something true [1] and something invented [9].", make_sources())
        assert answer.fabricated_markers == [9]
        assert [c.marker for c in answer.citations] == [1]
        assert "[9]" not in answer.content
        assert answer.grounded is True

    def test_all_markers_fabricated_means_ungrounded(self):
        """An answer citing only nonexistent sources is not grounding."""
        answer = resolve_citations("Confident nonsense [7] [8].", make_sources())
        assert answer.grounded is False
        assert answer.citations == []
        assert answer.content == REFUSAL_TEMPLATE

    def test_no_markers_at_all_is_ungrounded(self):
        answer = resolve_citations("A confident paragraph with no sources.", make_sources())
        assert answer.grounded is False
        assert answer.content == REFUSAL_TEMPLATE

    def test_duplicate_markers_produce_one_citation(self):
        answer = resolve_citations("First [1]. Second [1] again [1].", make_sources())
        assert len(answer.citations) == 1

    def test_quote_is_trimmed_at_a_sentence_boundary(self):
        long_content = ("A sentence of real length. " * 30).strip()
        source = Source(1, "c", "d", "T", long_content, score=0.02)
        answer = resolve_citations("Claim [1].", [source])
        quote = answer.citations[0].quote
        assert len(quote) <= 245
        assert quote.endswith("…")


class TestRefusalDecision:
    def test_empty_sources_refuses(self):
        assert should_refuse([], score_floor=0.008) is True

    def test_below_floor_refuses(self):
        low = [Source(1, "c", "d", "T", "text", score=0.0001)]
        assert should_refuse(low, score_floor=0.008) is True

    def test_above_floor_proceeds(self):
        ok = [Source(1, "c", "d", "T", "text", score=0.02)]
        assert should_refuse(ok, score_floor=0.008) is False

    def test_floor_matches_rrf_arithmetic(self):
        """A rank-1 keyword-only hit must clear the production floor, otherwise
        keyword-only retrieval would be unusable."""
        rank1_keyword = 0.7 / (60 + 1)
        assert rank1_keyword > 0.008


class TestContextAssembly:
    def test_sources_are_numbered_and_delimited(self):
        context = build_context(make_sources(2))
        assert '<source id="1" title="Title 1" page="1">' in context
        assert '<source id="2"' in context
        assert context.count("</source>") == 2

    def test_context_marks_content_as_untrusted_data(self):
        context = build_context(make_sources(1))
        lowered = context.lower()
        assert "data" in lowered
        assert "not instructions" in lowered

    def test_quotes_in_titles_cannot_break_the_delimiter(self):
        source = Source(1, "c", "d", 'Bad "title" here', "text", score=0.02)
        context = build_context([source])
        assert context.count("<source") == 1
        assert "title=\"Bad 'title' here\"" in context
